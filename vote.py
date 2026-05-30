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
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

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
    # Inverser : texte blanc sur fond coloré → texte noir sur fond clair
    image = ImageOps.invert(image)
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(2.5)
    image = image.filter(ImageFilter.SHARPEN)
    image = image.point(lambda x: 0 if x < 128 else 255, "1")
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
    processed.save("screenshot_captcha_processed.png")
    config = "--psm 7 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    text = pytesseract.image_to_string(processed, config=config)
    return text.strip().replace(" ", "").replace("\n", "")


def enter_pseudo(driver):
    for sel in ["input[name='username']", "input[name='pseudo']",
                "input[placeholder*='pseudo' i]", "input[placeholder*='Pseudo']",
                "input[placeholder*='pseudonyme' i]", "input[type='text']"]:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el and el.is_displayed():
                el.clear()
                time.sleep(0.2)
                el.send_keys(PSEUDO)
                print(f"  Pseudo entré ({sel})")
                return True
        except Exception:
            continue
    return False


def type_in_captcha_input(driver, iframe, text):
    # Méthode 1 : switch_to.frame
    try:
        driver.switch_to.frame(iframe)
        for sel in ["#mtcaptcha-verifyinput", "input[id*='verify']",
                    "input[id*='captcha']", "input[type='text']", "input"]:
            try:
                inp = driver.find_element(By.CSS_SELECTOR, sel)
                if inp:
                    inp.clear()
                    inp.send_keys(text)
                    print(f"  Tapé dans iframe ({sel})")
                    driver.switch_to.default_content()
                    return
            except Exception:
                continue
        driver.switch_to.default_content()
    except Exception as e:
        driver.switch_to.default_content()
        print(f"  frame switch: {e}")

    # Méthode 2 : ActionChains
    size = iframe.size
    input_x = int(size['width'] * 0.15)
    input_y = int(size['height'] * 0.50)
    actions = ActionChains(driver)
    actions.move_to_element_with_offset(iframe, input_x, input_y)
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


def verifier_vote(driver):
    if not VELTHAR_PASSWORD:
        return
    print("\n--- Vérification Velthar ---")
    try:
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
        print(f"  Login: {driver.current_url}")
        print("  Attente 20s...")
        time.sleep(20)

        driver.get(f"{VELTHAR_URL}/vote")
        time.sleep(5)
        driver.save_screenshot("screenshot_velthar_vote.png")

        all_btns = driver.find_elements(By.CSS_SELECTOR, "button, a, input[type='submit']")
        print(f"  Boutons: {[b.text.strip() for b in all_btns if b.text.strip()][:10]}")

        for btn in all_btns:
            txt = (btn.text or "").strip().upper()
            if "VERIF" in txt or "VÉRIF" in txt:
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(3)
                print("  VOTE VÉRIFIÉ!")
                return

        print("  Bouton VÉRIFIER introuvable")

    except Exception as e:
        print(f"  Erreur: {e}")


def vote():
    print(f"[{time.strftime('%H:%M:%S')}] Vote pour {PSEUDO}")
    driver = get_driver()
    try:
        driver.get(VOTE_URL)
        time.sleep(8)
        driver.save_screenshot("screenshot_page.png")
        print(f"  Title: {driver.title}")

        if "just a moment" in driver.title.lower():
            print("  Cloudflare, attente...")
            time.sleep(10)

        enter_pseudo(driver)
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
                    time.sleep(2)
                    continue

                # Re-entrer le pseudo si nécessaire
                enter_pseudo(driver)
                time.sleep(0.3)

                type_in_captcha_input(driver, iframe, captcha_text)
                time.sleep(0.5)

                vote_button = driver.find_element(By.CSS_SELECTOR,
                    "button[type='submit'], input[type='submit']")
                vote_button.click()
                time.sleep(4)

                driver.save_screenshot(f"screenshot_apres_vote_{attempt}.png")
                page = driver.page_source.lower()

                for kw in ["validé", "valide", "erreur", "captcha"]:
                    idx = page.find(kw)
                    if idx >= 0:
                        print(f"  '{kw}': ...{page[max(0,idx-20):idx+80]}...")

                if any(w in page for w in ["vote validé", "vote valide", "votre vote a"]):
                    print("VOTE REUSSI!")
                    success = True
                    break
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

        if not success:
            driver.save_screenshot("screenshot_echec.png")
            print("Echec apres 6 tentatives")
            sys.exit(1)

        verifier_vote(driver)

    finally:
        driver.quit()


vote()
