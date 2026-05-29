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


CAPTCHA_SELECTORS = [
    "img.mtcaptcha-verifyimage",
    "canvas.mtcaptcha-verifyimage",
    "#mtcaptcha img",
    "#mtcaptcha canvas",
    "[id*='mtcaptcha'] img",
    "[id*='mtcaptcha'] canvas",
    "img[src*='mtcaptcha']",
    ".mtcaptcha img",
    ".mtcaptcha canvas",
]


def find_captcha_element(driver):
    # Cherche dans la page principale
    for sel in CAPTCHA_SELECTORS:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el:
                print(f"  Captcha trouvé (page principale): {sel}")
                return el, False
        except Exception:
            continue

    # Cherche dans les iframes
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    print(f"  {len(iframes)} iframe(s) détecté(s)")
    for i, iframe in enumerate(iframes):
        try:
            src = iframe.get_attribute("src") or ""
            print(f"  iframe {i}: {src[:80]}")
            driver.switch_to.frame(iframe)
            for sel in CAPTCHA_SELECTORS:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                    if el:
                        print(f"  Captcha trouvé (iframe {i}): {sel}")
                        return el, True
                except Exception:
                    continue
            driver.switch_to.default_content()
        except Exception:
            driver.switch_to.default_content()

    # Debug: liste toutes les images
    driver.switch_to.default_content()
    imgs = driver.find_elements(By.TAG_NAME, "img")
    print(f"  {len(imgs)} image(s) sur la page:")
    for img in imgs[:8]:
        src = (img.get_attribute("src") or "")[:80]
        cls = img.get_attribute("class") or ""
        print(f"    class='{cls}' src='{src}'")

    return None, False


def get_captcha_text(driver):
    el, in_iframe = find_captcha_element(driver)
    if not el:
        raise Exception("Image CAPTCHA introuvable")

    tag = el.tag_name
    if tag == "canvas":
        data_url = driver.execute_script("return arguments[0].toDataURL('image/png');", el)
        img_data = io.BytesIO(
            __import__('base64').b64decode(data_url.split(',')[1])
        )
        image = Image.open(img_data)
    else:
        image = Image.open(io.BytesIO(el.screenshot_as_png))

    if in_iframe:
        driver.switch_to.default_content()

    processed = preprocess_image(image)
    config = "--psm 8 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    text = pytesseract.image_to_string(processed, config=config)
    return text.strip().replace(" ", "").replace("\n", "")


def find_captcha_input(driver):
    selectors = [
        "#mtcaptcha-verifyinput",
        "input[name='captcha']",
        "input[id*='captcha' i]",
        "input[placeholder*='captcha' i]",
        ".mtcaptcha input",
    ]
    # Essaie page principale
    for sel in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el:
                return el, False
        except Exception:
            continue
    # Essaie iframes
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    for iframe in iframes:
        try:
            driver.switch_to.frame(iframe)
            for sel in selectors:
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
        btn = driver.find_element(By.CSS_SELECTOR,
            "[id*='refresh'], [class*='reload'], .mtcaptcha-reload")
        btn.click()
        time.sleep(2)
    except Exception:
        pass


def vote():
    print(f"[{time.strftime('%H:%M:%S')}] Tentative de vote pour {PSEUDO}")
    driver = get_driver()
    try:
        driver.get(VOTE_URL)
        time.sleep(8)

        driver.save_screenshot("screenshot_page.png")
        print(f"  Title: {driver.title}")

        # Remplir le pseudo
        input_selectors = [
            "input[name='pseudo']", "input[name='username']",
            "input[name='nickname']", "input[placeholder*='pseudo' i]",
            "input[placeholder*='joueur' i]", "input[type='text']",
        ]
        username_input = None
        for sel in input_selectors:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el and el.is_displayed():
                    username_input = el
                    print(f"  Champ pseudo: {sel}")
                    break
            except Exception:
                continue

        if not username_input:
            print("ERREUR: Champ pseudo introuvable")
            sys.exit(1)

        username_input.clear()
        time.sleep(0.5)
        username_input.send_keys(PSEUDO)
        print(f"  Pseudo '{PSEUDO}' entré")
        time.sleep(5)

        success = False
        for attempt in range(6):
            print(f"Tentative OCR #{attempt + 1}/6")
            try:
                captcha_text = get_captcha_text(driver)
                print(f"  Texte: '{captcha_text}'")
                if len(captcha_text) < 3:
                    refresh_captcha(driver)
                    continue

                captcha_input, in_iframe = find_captcha_input(driver)
                if not captcha_input:
                    print("  Champ captcha introuvable")
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
                    refresh_captcha(driver)
            except Exception as e:
                print(f"  Erreur: {e}")
                time.sleep(1)

        if not success:
            driver.save_screenshot("screenshot_echec.png")
            print("Echec apres 6 tentatives")
            sys.exit(1)
    finally:
        driver.quit()


vote()
