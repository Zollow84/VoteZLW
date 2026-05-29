import time
import io
import sys

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter

PSEUDO = "Zollow"
VOTE_URL = "https://serveur-prive.net/minecraft/velthar/vote"


def get_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


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


def get_captcha_text(driver):
    selectors = [
        "img.mtcaptcha-verifyimage",
        "#mtcaptcha img",
        "[id*='mtcaptcha'] img",
        "img[src*='mtcaptcha']",
        ".mtcaptcha img",
    ]
    captcha_img = None
    for sel in selectors:
        try:
            captcha_img = driver.find_element(By.CSS_SELECTOR, sel)
            if captcha_img:
                break
        except Exception:
            continue
    if not captcha_img:
        raise Exception("Image CAPTCHA introuvable")
    img_bytes = captcha_img.screenshot_as_png
    image = Image.open(io.BytesIO(img_bytes))
    processed = preprocess_image(image)
    config = "--psm 8 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    text = pytesseract.image_to_string(processed, config=config)
    return text.strip().replace(" ", "").replace("\n", "")


def find_username_input(driver):
    selectors = [
        "input[name='pseudo']",
        "input[name='username']",
        "input[name='nickname']",
        "input[name='name']",
        "input[placeholder*='pseudo' i]",
        "input[placeholder*='username' i]",
        "input[placeholder*='joueur' i]",
        "input[placeholder*='nom' i]",
        "input[type='text']",
    ]
    for sel in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el and el.is_displayed():
                print(f"  Champ pseudo trouvé avec: {sel}")
                return el
        except Exception:
            continue
    return None


def refresh_captcha(driver):
    try:
        refresh_btn = driver.find_element(By.CSS_SELECTOR,
            "[id*='refresh'], [class*='reload'], .mtcaptcha-reload")
        refresh_btn.click()
        time.sleep(2)
    except Exception:
        pass


def vote():
    print(f"[{time.strftime('%H:%M:%S')}] Tentative de vote pour {PSEUDO}")
    driver = get_driver()
    try:
        driver.get(VOTE_URL)
        time.sleep(5)

        # Screenshot pour debug
        driver.save_screenshot("screenshot_page.png")
        print(f"  Page title: {driver.title}")
        print(f"  URL actuelle: {driver.current_url}")

        username_input = find_username_input(driver)
        if not username_input:
            print("ERREUR: Champ pseudo introuvable")
            print("  HTML snippet:", driver.find_element(By.TAG_NAME, "body").get_attribute("innerHTML")[:2000])
            sys.exit(1)

        username_input.clear()
        time.sleep(0.5)
        username_input.send_keys(PSEUDO)
        print(f"  Pseudo '{PSEUDO}' entré")
        time.sleep(4)

        success = False
        for attempt in range(6):
            print(f"Tentative OCR #{attempt + 1}/6")
            try:
                captcha_text = get_captcha_text(driver)
                print(f"  Texte: '{captcha_text}'")
                if len(captcha_text) < 3:
                    refresh_captcha(driver)
                    continue
                captcha_input = driver.find_element(By.CSS_SELECTOR,
                    "#mtcaptcha-verifyinput, input[name='captcha'], input[id*='captcha' i], .mtcaptcha input")
                captcha_input.clear()
                captcha_input.send_keys(captcha_text)
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
