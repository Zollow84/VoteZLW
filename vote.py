import time
import io
import sys
import subprocess
import re
import os
import base64

import requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

PSEUDO = "Zollow"
VOTE_URL = "https://serveur-prive.net/minecraft/velthar/vote"
VELTHAR_URL = "https://velthar.fr"
VELTHAR_PASSWORD = os.environ.get("VELTHAR_PASSWORD", "")
TWOCAPTCHA_API_KEY = os.environ.get("TWOCAPTCHA_API_KEY", "")

# Proxy résidentiel (authentifié)
PROXY_HOST = os.environ.get("PROXY_HOST", "")
PROXY_PORT = os.environ.get("PROXY_PORT", "")
PROXY_USER = os.environ.get("PROXY_USER", "")
PROXY_PASS = os.environ.get("PROXY_PASS", "")

LOCAL_PROXY_PORT = 8899
CAPTCHA_2CAPTCHA_IMG = "screenshot_captcha_2captcha.png"

_proxy_proc = None


def get_chrome_version():
    try:
        result = subprocess.run(['google-chrome', '--version'], capture_output=True, text=True)
        match = re.search(r'(\d+)\.', result.stdout)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return None


def start_local_proxy():
    """Lance pproxy : relais local 127.0.0.1 -> proxy authentifié upstream (tunnel, sans interception SSL)."""
    global _proxy_proc
    if _proxy_proc is not None:
        return
    remote = f"http://{PROXY_HOST}:{PROXY_PORT}#{PROXY_USER}:{PROXY_PASS}"
    cmd = [sys.executable, "-m", "pproxy",
           "-l", f"http://127.0.0.1:{LOCAL_PROXY_PORT}",
           "-r", remote]
    _proxy_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(4)
    print(f"  Proxy local pproxy:{LOCAL_PROXY_PORT} -> {PROXY_HOST}:{PROXY_PORT}")


def stop_local_proxy():
    global _proxy_proc
    if _proxy_proc:
        try:
            _proxy_proc.terminate()
        except Exception:
            pass
        _proxy_proc = None


def get_driver(use_proxy=True):
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,900")

    version = get_chrome_version()
    if version:
        print(f"  Chrome version: {version}")

    if use_proxy and PROXY_HOST and PROXY_PORT and PROXY_USER:
        start_local_proxy()
        options.add_argument(f"--proxy-server=http://127.0.0.1:{LOCAL_PROXY_PORT}")
    elif use_proxy and PROXY_HOST and PROXY_PORT:
        options.add_argument(f"--proxy-server=http://{PROXY_HOST}:{PROXY_PORT}")
        print(f"  Proxy: {PROXY_HOST}:{PROXY_PORT}")
    elif not use_proxy:
        print("  (connexion directe, sans proxy)")

    return uc.Chrome(options=options, use_subprocess=True, version_main=version)


def check_ip(driver):
    """Affiche l'IP utilisée (debug proxy)."""
    try:
        driver.get("https://api.ipify.org")
        time.sleep(2)
        ip = driver.find_element(By.TAG_NAME, "body").text.strip()
        print(f"  IP sortie: {ip}")
    except Exception as e:
        print(f"  IP check erreur: {e}")


def prepare_captcha_images(iframe):
    """Screenshot l'iframe, crop sur le texte, sauve une image propre pour 2captcha
    et retourne le crop couleur (pour OCR de secours)."""
    img_bytes = iframe.screenshot_as_png
    full_img = Image.open(io.BytesIO(img_bytes))
    full_img.save("screenshot_captcha_iframe.png")
    w, h = full_img.size
    print(f"  Iframe: {w}x{h}px")

    crop = full_img.crop((int(w * 0.33), 2, int(w * 0.87), int(h * 0.62)))
    crop.save("screenshot_captcha_crop.png")

    clean = crop.convert("RGB").resize((crop.width * 4, crop.height * 4), Image.LANCZOS)
    clean = ImageEnhance.Contrast(clean).enhance(1.5)
    clean.save(CAPTCHA_2CAPTCHA_IMG)

    return crop


def solve_with_2captcha(image_path):
    """Envoie l'image à 2captcha et récupère le texte résolu par un humain."""
    if not TWOCAPTCHA_API_KEY:
        return ""
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        resp = requests.post("http://2captcha.com/in.php", data={
            "key": TWOCAPTCHA_API_KEY,
            "method": "base64",
            "body": b64,
            "json": 1,
            "phrase": 0,
            "case": 1,
            "numeric": 0,
            "min_len": 3,
            "max_len": 8,
        }, timeout=30)
        data = resp.json()
        if data.get("status") != 1:
            print(f"  2captcha soumission erreur: {data.get('request')}")
            return ""

        cid = data["request"]
        print(f"  2captcha id={cid}, attente solution...")

        for _ in range(24):  # ~120s max
            time.sleep(5)
            r = requests.get("http://2captcha.com/res.php", params={
                "key": TWOCAPTCHA_API_KEY,
                "action": "get",
                "id": cid,
                "json": 1,
            }, timeout=30)
            rd = r.json()
            if rd.get("status") == 1:
                sol = (rd.get("request") or "").strip()
                print(f"  2captcha solution: '{sol}'")
                return sol
            if rd.get("request") != "CAPCHA_NOT_READY":
                print(f"  2captcha erreur: {rd.get('request')}")
                return ""

        print("  2captcha timeout")
        return ""
    except Exception as e:
        print(f"  2captcha exception: {e}")
        return ""


def ocr_captcha(crop):
    """OCR Tesseract de secours si 2captcha indisponible."""
    cfg_word  = "--psm 8 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    cfg_line  = "--psm 7 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

    def prep(img, invert=False, threshold=128, scale=4):
        img = img.convert("RGB").resize((img.width * scale, img.height * scale), Image.LANCZOS)
        img = img.convert("L")
        if invert:
            img = ImageOps.invert(img)
        img = ImageEnhance.Contrast(img).enhance(3.0)
        img = img.filter(ImageFilter.SHARPEN)
        return img.point(lambda x: 0 if x < threshold else 255, "1")

    variants = [
        (prep(crop.copy(), invert=True,  threshold=80),  cfg_word),
        (prep(crop.copy(), invert=False, threshold=200), cfg_word),
        (prep(crop.copy(), invert=True,  threshold=120), cfg_line),
    ]

    best = ""
    for i, (processed, cfg) in enumerate(variants, 1):
        try:
            text = pytesseract.image_to_string(processed, config=cfg)
            text = text.strip().replace(" ", "").replace("\n", "")
            print(f"  OCR v{i}: '{text}'")
            if len(text) > len(best):
                best = text
        except Exception as e:
            print(f"  OCR v{i} erreur: {e}")
    return best


def read_captcha(iframe):
    """Lit le captcha : 2captcha en priorité, OCR Tesseract en secours."""
    crop = prepare_captcha_images(iframe)
    if TWOCAPTCHA_API_KEY:
        sol = solve_with_2captcha(CAPTCHA_2CAPTCHA_IMG)
        if sol:
            return sol
        print("  2captcha a échoué, fallback OCR")
    return ocr_captcha(crop)


def get_mtcaptcha_iframe(driver):
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    for iframe in iframes:
        src = iframe.get_attribute("src") or ""
        if "mtcaptcha" in src:
            return iframe
    return None


def snapshot_input_values(driver):
    values = set()
    try:
        for el in driver.find_elements(By.TAG_NAME, "input"):
            try:
                v = el.get_attribute('value') or ''
                if len(v) > 10:
                    values.add(v)
            except Exception:
                continue
    except Exception:
        pass
    return values


def find_new_verified_token(driver, before_values):
    for js in [
        "return window.mtcaptcha ? window.mtcaptcha.getVerifiedToken() : null",
        "return typeof mtcaptcha !== 'undefined' ? mtcaptcha.getVerifiedToken() : null",
    ]:
        try:
            token = driver.execute_script(js)
            if token and len(token) > 20:
                print(f"  Token MTCaptcha (JS): '{token[:25]}...'")
                return token
        except Exception:
            pass

    try:
        for el in driver.find_elements(By.TAG_NAME, "input"):
            try:
                name = el.get_attribute('name') or ''
                val  = el.get_attribute('value') or ''
                if name == '_token':
                    continue
                if len(val) > 20 and val not in before_values:
                    print(f"  Nouveau token (input name='{name}'): '{val[:25]}...'")
                    return val
            except Exception:
                continue
    except Exception:
        pass

    return ""


def enter_pseudo(driver):
    for sel in ["input[name='username']", "input[name='pseudo']",
                "input[placeholder*='pseudonyme' i]", "input[placeholder*='pseudo' i]",
                "input[type='text']"]:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el and el.is_displayed():
                el.clear()
                time.sleep(0.2)
                el.send_keys(PSEUDO)
                return True
        except Exception:
            continue
    return False


def type_in_captcha_input(driver, iframe, text):
    try:
        driver.switch_to.frame(iframe)
        for sel in ["#mtcaptcha-verifyinput", "input[id*='verify']",
                    "input[id*='captcha']", "input[type='text']", "input"]:
            try:
                inp = driver.find_element(By.CSS_SELECTOR, sel)
                if inp:
                    inp.click()
                    time.sleep(0.2)
                    inp.clear()
                    for char in text:
                        inp.send_keys(char)
                        time.sleep(0.05)
                    driver.execute_script("""
                        var el = arguments[0];
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'a'}));
                    """, inp)
                    time.sleep(0.5)
                    print(f"  Tapé + events ({sel}): '{text}'")
                    driver.switch_to.default_content()
                    return
            except Exception:
                continue
        driver.switch_to.default_content()
    except Exception as e:
        driver.switch_to.default_content()
        print(f"  Erreur frame: {e}")

    size = iframe.size
    actions = ActionChains(driver)
    actions.move_to_element_with_offset(iframe, int(size['width'] * 0.15), int(size['height'] * 0.50))
    actions.click()
    actions.pause(0.3)
    actions.key_down(Keys.CONTROL).send_keys('a').key_up(Keys.CONTROL)
    actions.send_keys(text)
    actions.perform()
    print("  Tapé via ActionChains")


def refresh_captcha_click(driver, iframe):
    size = iframe.size
    actions = ActionChains(driver)
    actions.move_to_element_with_offset(iframe, int(size['width'] * 0.90), int(size['height'] * 0.50))
    actions.click()
    actions.perform()
    time.sleep(2)


def login_velthar(driver):
    """Se connecte au compte Velthar."""
    print("--- Connexion Velthar ---")
    driver.get("https://velthar.fr/auth/login")
    time.sleep(4)

    for sel in ["input[name='name']", "input[name='username']", "input[type='text']"]:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el and el.is_displayed():
                el.clear()
                el.send_keys(PSEUDO)
                break
        except Exception:
            continue

    for sel in ["input[type='password']", "input[name='password']"]:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el:
                el.clear()
                el.send_keys(VELTHAR_PASSWORD)
                break
        except Exception:
            continue

    for sel in ["button[type='submit']", "button"]:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el and el.is_displayed():
                el.click()
                break
        except Exception:
            continue

    time.sleep(4)
    print(f"  Login Velthar: {driver.current_url}")


def click_voter_maintenant(driver):
    """Déclenche l'action Livewire selectWebsite (serveur-prive) SANS naviguer (garde Velthar vivant),
    puis ouvre serveur-prive dans un onglet séparé pour voter."""
    driver.get(f"{VELTHAR_URL}/vote")
    time.sleep(5)
    driver.save_screenshot("screenshot_velthar_avant_clic.png")

    # Déclencher wire:click="selectWebsite(...)" du bloc serveur-prive, en retirant le href
    # du <a> parent pour empêcher l'onglet Velthar de naviguer (il doit rester vivant).
    clicked = driver.execute_script("""
        var els = document.querySelectorAll('div, a, button');
        for (var i = 0; i < els.length; i++) {
            var w = els[i].getAttribute('wire:click');
            if (!w) continue;
            var a = els[i].closest('a');
            var href = a ? (a.getAttribute('href') || '') : '';
            if (href.indexOf('serveur-prive') !== -1) {
                if (a) { a.removeAttribute('href'); a.removeAttribute('target'); }
                els[i].click();
                return w + ' | ' + href;
            }
        }
        return null;
    """)
    print(f"  Livewire selectWebsite déclenché: {clicked}")
    if not clicked:
        print("  wire:click serveur-prive introuvable")

    time.sleep(6)  # laisser Livewire mettre à jour la page (-> VÉRIFIER MON VOTE)
    driver.save_screenshot("screenshot_velthar_apres_select.png")

    # Ouvrir serveur-prive dans un NOUVEL onglet via l'API Selenium fiable
    # (NE touche PAS l'onglet Velthar qui reste vivant avec son bouton VÉRIFIER MON VOTE)
    driver.switch_to.new_window('tab')
    driver.get(VOTE_URL)
    time.sleep(3)
    print(f"  Onglet serveur-prive (nouveau): {driver.current_url}")
    return True


def do_captcha_vote(driver):
    """Effectue le vote sur serveur-prive. Retourne 'ok', 'already' ou 'fail'."""
    enter_pseudo(driver)
    time.sleep(5)

    for attempt in range(6):
        print(f"Tentative #{attempt + 1}/6")
        try:
            iframe = get_mtcaptcha_iframe(driver)
            if not iframe:
                print("  Iframe introuvable")
                time.sleep(2)
                continue

            captcha_text = read_captcha(iframe)
            print(f"  Captcha lu: '{captcha_text}'")

            if len(captcha_text) < 3:
                refresh_captcha_click(driver, iframe)
                time.sleep(2)
                continue

            enter_pseudo(driver)
            time.sleep(0.3)

            before_values = snapshot_input_values(driver)

            type_in_captcha_input(driver, iframe, captcha_text)
            time.sleep(5)

            token = find_new_verified_token(driver, before_values)

            if not token:
                print("  Pas de token MTCaptcha validé → captcha faux, refresh")
                iframe = get_mtcaptcha_iframe(driver)
                if iframe:
                    refresh_captcha_click(driver, iframe)
                time.sleep(2)
                continue

            print("  Token MTCaptcha validé, soumission...")
            vote_button = driver.find_element(By.CSS_SELECTOR,
                "button[type='submit'], input[type='submit']")
            vote_button.click()
            time.sleep(4)

            driver.save_screenshot(f"screenshot_apres_vote_{attempt}.png")
            page = driver.page_source.lower()

            for kw in ["validé", "valide", "erreur", "captcha", "ip"]:
                idx = page.find(kw)
                if idx >= 0:
                    print(f"  '{kw}': ...{page[max(0,idx-20):idx+80]}...")

            if any(w in page for w in ["vote validé", "vote valide", "votre vote a"]):
                print("VOTE REUSSI!")
                return "ok"
            elif "déjà voté" in page or "deja vote" in page:
                print("  Déjà voté sur serveur-prive (cooldown)")
                return "already"
            elif "ip n'est pas autorisée" in page or "ip n est pas autorisee" in page:
                print("  ERREUR IP : l'IP n'est pas autorisée à voter")
                return "fail"
            else:
                print("  Non confirmé, refresh...")
                iframe = get_mtcaptcha_iframe(driver)
                if iframe:
                    refresh_captcha_click(driver, iframe)
                time.sleep(2)

        except Exception as e:
            print(f"  Erreur: {e}")
            driver.switch_to.default_content()
            time.sleep(1)

    return "fail"


def claim_velthar(driver, velthar_handle=None):
    """Revient sur l'onglet Velthar VIVANT (sans recharger, pour garder l'état Livewire) et clique 'VÉRIFIER MON VOTE'."""
    print("\n--- Vérification Velthar (VÉRIFIER MON VOTE) ---")
    if velthar_handle and velthar_handle in driver.window_handles:
        driver.switch_to.window(velthar_handle)
        print("  Retour sur l'onglet Velthar vivant (sans recharger)")
    else:
        driver.get(f"{VELTHAR_URL}/vote")
        time.sleep(5)

    for essai in range(10):
        time.sleep(5)
        driver.save_screenshot(f"screenshot_velthar_verif_{essai}.png")

        # DIAGNOSTIC : sommes-nous connectés à Velthar ?
        page = driver.page_source.lower()
        connecte = ("zollow" in page) or ("déconnexion" in page) or ("vous avez" in page and "vote" in page)
        print(f"  [diag] Connecté Velthar: {connecte} | URL: {driver.current_url}")

        all_els = driver.find_elements(By.CSS_SELECTOR, "button, a, input[type='submit'], div")
        verif_btn = None
        for el in all_els:
            txt = (el.text or "").strip().upper()
            if ("VERIF" in txt or "VÉRIF" in txt) and len(txt) < 40:
                verif_btn = el
                break

        boutons = [e.text.strip() for e in driver.find_elements(By.CSS_SELECTOR, "button, a") if e.text.strip()]
        print(f"  Essai {essai + 1}/10 - Boutons: {boutons[:14]}")

        if verif_btn:
            driver.execute_script("arguments[0].click();", verif_btn)
            time.sleep(4)
            driver.save_screenshot("screenshot_velthar_verifie.png")
            print("  VOTE VÉRIFIÉ sur Velthar!")
            return True

        print("  Bouton VÉRIFIER pas encore là, attente 10s...")
        time.sleep(10)

    print("  Bouton VÉRIFIER introuvable")
    return False


def vote():
    print(f"[{time.strftime('%H:%M:%S')}] Vote pour {PSEUDO}")
    if TWOCAPTCHA_API_KEY:
        print("  Mode captcha: 2captcha (résolution humaine)")
    else:
        print("  Mode captcha: OCR Tesseract (TWOCAPTCHA_API_KEY absent)")

    driver = get_driver(use_proxy=True)
    try:
        if PROXY_HOST:
            check_ip(driver)

        velthar_flow = bool(VELTHAR_PASSWORD)

        velthar_handle = None
        if velthar_flow:
            print("\n=== Connexion Velthar + Livewire selectWebsite ===")
            login_velthar(driver)
            velthar_handle = driver.current_window_handle
            if not click_voter_maintenant(driver):
                print("  Fallback: navigation directe serveur-prive")
                driver.get(VOTE_URL)
        else:
            driver.get(VOTE_URL)

        print("\n=== Vote serveur-prive ===")
        time.sleep(8)
        driver.save_screenshot("screenshot_page.png")
        print(f"  Title: {driver.title}")

        if "just a moment" in driver.title.lower():
            print("  Cloudflare, attente...")
            time.sleep(10)

        result = do_captcha_vote(driver)
        print(f"  Résultat serveur-prive: {result}")

        if result == "fail":
            print("Echec du vote serveur-prive")
            sys.exit(1)

        if velthar_flow:
            claim_velthar(driver, velthar_handle)

    finally:
        driver.quit()
        stop_local_proxy()


vote()
