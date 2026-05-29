import time
import schedule
import io
import sys

try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter
except ImportError as e:
    print(f"Module manquant: {e}")
    print("Lance d'abord: pip install undetected-chromedriver selenium pytesseract Pillow schedule")
    sys.exit(1)

# ========== CONFIG ==========
PSEUDO = "Zollow"
VOTE_URL = "https://serveur-prive.net/minecraft/velthar/vote"
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
# ============================

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


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
        raise Exception("Image CAPTCHA introuvable dans la page")

    img_bytes = captcha_img.screenshot_as_png
    image = Image.open(io.BytesIO(img_bytes))
    processed = preprocess_image(image)

    config = "--psm 8 --oem 3 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    text = pytesseract.image_to_string(processed, config=config)
    return text.strip().replace(" ", "").replace("\n", "")


def refresh_captcha(driver):
    try:
        refresh_btn = driver.find_element(
            By.CSS_SELECTOR,
            "[id*='refresh'], [class*='reload'], .mtcaptcha-reload, [title*='refresh' i]",
        )
        refresh_btn.click()
        time.sleep(2)
    except Exception:
        pass


def vote():
    print(f"\n[{time.strftime('%H:%M:%S')}] === Tentative de vote ===")

    options = uc.ChromeOptions()
    driver = uc.Chrome(options=options)

    try:
        driver.get(VOTE_URL)
        wait = WebDriverWait(driver, 15)

        # Remplir le pseudo
        username_input = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[name='pseudo'], input[placeholder*='pseudo' i], input[placeholder*='Pseudo']")
            )
        )
        username_input.clear()
        time.sleep(0.5)
        username_input.send_keys(PSEUDO)
        print(f"Pseudo '{PSEUDO}' entré")

        # Attendre le chargement du widget captcha
        time.sleep(4)

        success = False
        for attempt in range(6):
            print(f"Tentative OCR #{attempt + 1}/6")

            try:
                captcha_text = get_captcha_text(driver)
                print(f"  Texte détecté: '{captcha_text}'")

                if len(captcha_text) < 3:
                    print("  Texte trop court, refresh captcha...")
                    refresh_captcha(driver)
                    continue

                # Trouver le champ de saisie du captcha
                captcha_input = driver.find_element(
                    By.CSS_SELECTOR,
                    "#mtcaptcha-verifyinput, input[name='captcha'], input[id*='captcha' i], input[placeholder*='captcha' i], .mtcaptcha input",
                )
                captcha_input.clear()
                captcha_input.send_keys(captcha_text)
                time.sleep(0.5)

                # Cliquer sur le bouton de vote
                vote_button = driver.find_element(
                    By.CSS_SELECTOR,
                    "button[type='submit'], input[type='submit'], .btn-vote, button.btn-primary",
                )
                vote_button.click()
                time.sleep(4)

                # Vérifier le succès
                page = driver.page_source.lower()
                if any(word in page for word in ["succès", "success", "voté", "merci", "vote validé"]):
                    print("  ✅ VOTE RÉUSSI!")
                    success = True
                    break
                else:
                    print("  Captcha incorrect, nouveau essai...")
                    refresh_captcha(driver)
                    time.sleep(1)

            except Exception as e:
                print(f"  Erreur: {e}")
                time.sleep(1)

        if not success:
            print("❌ Échec après 6 tentatives - le captcha était trop difficile à lire")

    except Exception as e:
        print(f"Erreur générale: {e}")
    finally:
        time.sleep(3)
        driver.quit()


print("=" * 45)
print("   Bot de vote Velthar - Pseudo: " + PSEUDO)
print("   Vote automatique toutes les 1h30")
print("   Ctrl+C pour arrêter")
print("=" * 45)

vote()

schedule.every(90).minutes.do(vote)

while True:
    schedule.run_pending()
    time.sleep(30)
