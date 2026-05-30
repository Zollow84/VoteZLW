import time
import io
import sys
import subprocess
import re
import os

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter

PSEUDO = "Zollow"
VOTE_URL = "https://serveur-prive.net/minecraft/velthar/vote"
VELTHAR_URL = "https://velthar.fr"
VELTHAR_PASSWORD = os.environ.get("VELTHAR_PASSWORD", "")


def get_chrome_version():
    try:
        result = subprocess.run(['google-chrome', '--version'], capture_output=True, text=True)
        match = re.search(r'(\d+)\.', result.stdout)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return None


def get_driver():
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,900")
    version = get_chrome_version()
    if version:
        print(f"  Chrome version: {version}")
    return uc.Chrome(options=options, use_subprocess=True, version_main=version)


def preprocess_image(image):
    image = image.convert("RGB")
    w, h = image.size
    image = image.resize((w * 4, h * 4), Image.LANCZOS)
    image = image.convert("L")
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(3.5)
    image = image.filter(ImageFilter.SHARPEN)
    image = image.point(lambda x: 0 if x < 140 else 255, "1")
    return image


def get_mtcaptcha_iframe(driver):
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    for iframe in iframes:
        src = iframe.get_attribute("src") or ""
        if "mtcaptcha" in src:
            return iframe
    return None


def ocr_captcha(iframe):
    img_bytes = iframe.screenshot_as_png
    full_img = Image.open(io.BytesIO(img_bytes))
    full_img.save("screenshot_captcha_iframe.png")
    w, h = full_img.size
    print(f"  Iframe: {w}x{h}px")
    crop = full_img.crop((int(w * 0.38), 2, int(w * 0.82), h - 2))
    crop.save("screenshot_captcha_crop.png")
    processed = preprocess_image(crop)
    config = "--psm 7 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    text = pytesseract.image_to_string(processed, config=config)
    return text.strip().replace(" ", "").replace("\n", "")


def type_in_captcha_input(driver, iframe, text):
    size = iframe.size
    input_x = int(size['width'] * 0.20)
    input_y = int(size['height'] * 0.50)
    actions = ActionChains(driver)
    actions.move_to_element_with_offset(iframe, input_x, input_y)
    actions.click()
    actions.pause(0.5)
    actions.key_down(Keys.CONTROL)
    actions.send_keys('a')
    actions.key_up(Keys.CONTROL)
    actions.send_keys(text)
    actions.perform()


def refresh_captcha_click(driver, iframe):
    size = iframe.size
    refresh_x = int(size['width'] * 0.90)
    refresh_y = int(size['height'] * 0.50)
    actions = ActionChains(driver)
    actions.move_to_element_with_offset(iframe, refresh_x, refresh_y)
    actions.click()
    actions.perform()
    time.sleep(2)


def verifier_vote(driver):
    if not VELTHAR_PASSWORD:
        print("  VELTHAR_PASSWORD non défini, skip")
        return

    print("\n--- Vérification du vote sur Velthar ---")
    try:
        driver.get("https://velthar.fr/auth/login")
        time.sleep(4)
        driver.save_screenshot("screenshot_velthar_login.png")
        print(f"  Login page title: {driver.title}")

        # Champ pseudo
        for sel in ["input[name='name']", "input[name='username']", "input[name='pseudo']",
                    "input[name='login']", "input[type='text']"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el and el.is_displayed():
                    el.clear()
                    el.send_keys(PSEUDO)
                    print(f"  Pseudo entré ({sel})")
                    break
            except Exception:
                continue

        # Champ mot de passe
        for sel in ["input[type='password']", "input[name='password']",
                    "input[name='mdp']", "input[name='pwd']"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el:
                    el.clear()
                    el.send_keys(VELTHAR_PASSWORD)
                    print(f"  Mot de passe entré ({sel})")
                    break
            except Exception:
                continue

        # Soumettre
        for sel in ["button[type='submit']", "input[type='submit']", "button"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el and el.is_displayed():
                    el.click()
                    break
            except Exception:
                continue

        time.sleep(4)
        print(f"  Après login: {driver.title} | {driver.current_url}")

        # Attendre que Velthar détecte le vote
        print("  Attente 20s pour que Velthar détecte le vote...")
        time.sleep(20)

        driver.get(f"{VELTHAR_URL}/vote")
        time.sleep(5)
        driver.save_screenshot("screenshot_velthar_vote.png")

        # Debug: liste tous les boutons
        all_btns = driver.find_elements(By.CSS_SELECTOR, "button, a, input[type='submit'], div[onclick]")
        print(f"  {len(all_btns)} bouton(s) sur la page:")
        for btn in all_btns[:15]:
            txt = (btn.text or btn.get_attribute("value") or "").strip()
            if txt:
                print(f"    '{txt}'")

        # Cherche VÉRIFIER MON VOTE
        clicked = False
        for btn in all_btns:
            txt = (btn.text or btn.get_attribute("value") or "").strip().upper()
            if "VERIF" in txt or "VÉRIF" in txt:
                try:
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(3)
                    driver.save_screenshot("screenshot_velthar_verifie.png")
                    print("  VOTE VÉRIFIÉ sur Velthar!")
                    clicked = True
                    break
                except Exception as e:
                    print(f"  Erreur click: {e}")

        if not clicked:
            print("  Bouton VÉRIFIER introuvable")

    except Exception as e:
        print(f"  Erreur vérification: {e}")
        driver.save_screenshot("screenshot_velthar_erreur.png")


def vote():
    print(f"[{time.strftime('%H:%M:%S')}] Tentative de vote pour {PSEUDO}")
    driver = get_driver()
    try:
        driver.get(VOTE_URL)
        time.sleep(8)
        driver.save_screenshot("screenshot_page.png")
        print(f"  Title: {driver.title}")

        if "just a moment" in driver.title.lower():
            print("  Cloudflare, attente...")
            time.sleep(10)

        for sel in ["input[name='username']", "input[name='pseudo']", "input[type='text']"]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el and el.is_displayed():
                    el.clear()
                    time.sleep(0.3)
                    el.send_keys(PSEUDO)
                    print(f"  Pseudo entré ({sel})")
                    break
            except Exception:
                continue

        time.sleep(5)

        success = False
        for attempt in range(6):
            print(f"Tentative #{attempt + 1}/6")
            try:
                iframe = get_mtcaptcha_iframe(driver)
                if not iframe:
                    print("  Iframe introuvable")
                    time.sleep(2)
                    continue

                captcha_text = ocr_captcha(iframe)
                print(f"  OCR: '{captcha_text}'")

                if len(captcha_text) < 3:
                    refresh_captcha_click(driver, iframe)
                    continue

                type_in_captcha_input(driver, iframe, captcha_text)
                time.sleep(0.5)

                vote_button = driver.find_element(By.CSS_SELECTOR,
                    "button[type='submit'], input[type='submit']")
                vote_button.click()
                time.sleep(4)

                page = driver.page_source.lower()
                if any(w in page for w in ["succès", "success", "voté", "merci", "vote validé"]):
                    print("VOTE REUSSI sur serveur-prive.net!")
                    success = True
                    break
                else:
                    iframe = get_mtcaptcha_iframe(driver)
                    if iframe:
                        refresh_captcha_click(driver, iframe)

            except Exception as e:
                print(f"  Erreur: {e}")
                time.sleep(1)

        if not success:
            driver.save_screenshot("screenshot_echec.png")
            print("Echec apres 6 tentatives")
            sys.exit(1)

        verifier_vote(driver)

    finally:
        driver.quit()


vote()
