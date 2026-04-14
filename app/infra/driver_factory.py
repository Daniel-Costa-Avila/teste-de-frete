from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from app.config import Settings


def build_driver(settings: Settings) -> webdriver.Remote:
    options = Options()

    # stable behavior for ecommerce pages
    options.add_argument("--window-size=1440,1200")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--lang=pt-BR")

    if settings.headless:
        # Prefer classic headless for better compatibility across Chromium builds.
        options.add_argument("--headless")

    if settings.chrome_binary_path:
        options.binary_location = settings.chrome_binary_path

    if settings.use_remote:
        options.set_capability("browserName", settings.browser_name)
        options.set_capability("browserVersion", settings.browser_version)
        options.set_capability("selenoid:options", {
            "enableVNC": True,
            "enableVideo": False,
            "sessionTimeout": "5m",
            "name": "freight-test-probel"
        })

        driver = webdriver.Remote(
            command_executor=settings.selenoid_url,
            options=options
        )
    else:
        service = Service(executable_path=settings.chromedriver_path) if settings.chromedriver_path else Service()
        driver = webdriver.Chrome(options=options, service=service)

    driver.set_page_load_timeout(settings.page_timeout_seconds)
    driver.implicitly_wait(0)  # priorizar explicit wait
    return driver
