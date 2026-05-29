import time
import io
import sys
import subprocess
import re

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter

PSEUDO = "Zollow"
VOTE_URL = "https://serveur-prive.net/minecraft/velthar/vote"


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
    options.add_argument("--disable-web-security")
    options.add_argument("--allow-running-insecure-content")
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


CAPTCHA_IMG_SELECTORS = [
    "img.mtcaptcha-verifyimage",
    "canvas.mtcaptcha-verifyimage",
    "[id*='mtcaptcha'] img",
    "[id*='mtcaptcha'] canvas",
    "img[src*='captcha']",
    "img[src*='verify']",
    "canvas",
    "img",
]

CAPTCHA_INPUT_SELECTORS = [
    "#mtcaptcha-verifyinput",
    "input[id*='verify']",
    "input[name*='captcha']",
    "input[type='text']",
]


def get_captcha_text(driver):
    # Cherche iframe mtcaptcha
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    mtcaptcha_iframe = None
    for iframe in iframes:
        src = iframe.get_attribute("src") or ""
        if "mtcaptcha" in src:
            mtcaptcha_iframe = iframe
            break

    if mtcaptcha_iframe:
        # Screenshot l'iframe entière (visuel, cross-origin ok)
        img_bytes = mtcaptcha_iframe.screenshot_as_png
        full_img = Image.open(io.BytesIO(img_bytes))
        full_img.save("screenshot_captcha_iframe.png")

        # Essaie d'accéder au DOM de l'iframe (fonctionne avec --disable-web-security)
        try:
            driver.switch_to.frame(mtcaptcha_iframe)
            for sel in CAPTCHA_IMG_SELECTORS:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                    if el:
                        print(f"  Captcha img trouvé dans iframe: {sel}")
                        img_bytes2 = el.screenshot_as_png
                        image = Image.open(io.BytesIO(img_bytes2))
                        image.save("screenshot_captcha_element.png")
                        driver.switch_to.default_content()
                        processed = preprocess_image(image)
                        config = "--psm 8 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
                        text = pytesseract.image_to_string(processed, config=config)
                        return text.strip().replace(" ", "").replace("\n", "")
                except Exception:
                    continue
            driver.switch_to.default_content()
        except Exception as e:
            print(f"  Accès iframe échoué: {e}")
            driver.switch_to.default_content()

        # Fallback: OCR sur l'iframe entière
        print("  Fallback: OCR sur iframe complète")
        w, h = full_img.size
        # Crop la partie droite (image captcha)
        cropped = full_img.crop((int(w * 0.35), 0, w - 30, h))
        cropped.save("screenshot_captcha_crop.png")
        processed = preprocess_image(cropped)
        config = "--psm 8 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        text = pytesseract.image_to_string(processed, config=config)
        return text.strip().replace(" ", "").replace("\n", "")

    raise Exception("Iframe MTCaptcha introuvable")


def find_captcha_input(driver):
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    for iframe in iframes:
        src = iframe.get_attribute("src") or ""
        if "mtcaptcha" in src:
            try:
                driver.switch_to.frame(iframe)
                for sel in CAPTCHA_INPUT_SELECTORS:
                    try:
                        el = driver.find_element(By.CSS_SELECTOR, sel)
                        if el:
                            return el, True
                    except Exception:
                        continue
                driver.switch_to.default_content()
            except Exception:
                driver.switch_to.default_content()
    return None, False


def refresh_captcha(driver):
    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for iframe in iframes:
            src = iframe.get_attribute("src") or ""
            if "mtcaptcha" in src:
                driver.switch_to.frame(iframe)
                btn = driver.find_element(By.CSS_SELECTOR,
                    "[id*='refresh'], [class*='reload'], button, [title*='refresh' i]")
                btn.click()
                driver.switch_to.default_content()
                time.sleep(2)
                return
    except Exception:
        driver.switch_to.default_content()


def vote():
    print(f"[{time.strftime('%H:%M:%S')}] Tentative de vote pour {PSEUDO}")
    driver = get_driver()
    try:
        driver.get(VOTE_URL)
        time.sleep(8)
        driver.save_screenshot("screenshot_page.png")
        print(f"  Title: {driver.title}")

        # Remplir pseudo
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
                captcha_text = get_captcha_text(driver)
                print(f"  Texte OCR: '{captcha_text}'")

                if len(captcha_text) < 3:
                    print("  Texte trop court, refresh...")
                    refresh_captcha(driver)
                    continue

                captcha_input, in_iframe = find_captcha_input(driver)
                if not captcha_input:
                    print("  Input captcha introuvable")
                    driver.switch_to.default_content()
                    continue

                captcha_input.clear()
                captcha_input.send_keys(captcha_text)
                if in_iframe:
                    driver.switch_to.default_content()
                time.sleep(0.5)

                vote_button = driver.find_element(By.CSS_SELECTOR,
                    "button[type='submit'], input[type='submit']")
                vote_button.click()
                time.sleep(4)

                page = driver.page_source.lower()
                if any(w in page for w in ["succès", "success", "voté", "merci", "vote validé"]):
                    print("VOTE REUSSI!")
                    success = True
                    break
                else:
                    print("  Incorrect, retry...")
                    refresh_captcha(driver)

            except Exception as e:
                print(f"  Erreur: {e}")
                driver.switch_to.default_content()
                time.sleep(1)

        if not success:
            driver.save_screenshot("screenshot_echec.png")
            print("Echec apres 6 tentatives")
            sys.exit(1)
    finally:
        driver.quit()


vote()
