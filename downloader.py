
import time
import requests

from tqdm import tqdm
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup


BASE_URL = "https://musicaldown.com"
START_URL = f"{BASE_URL}/id"

OUTPUT_DIR = Path("downloads")
OUTPUT_DIR.mkdir(exist_ok=True)

REQUEST_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 120
DELAY_BETWEEN_DOWNLOADS = 2

session = requests.Session()

session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
})


def is_tiktok_url(url):
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""

        return (
            parsed.scheme in ("http", "https")
            and (
                hostname == "tiktok.com"
                or hostname.endswith(".tiktok.com")
            )
        )

    except ValueError:
        return False


def get_download_form(tiktok_url):
    page = session.get(
        START_URL,
        timeout=REQUEST_TIMEOUT
    )

    page.raise_for_status()

    soup = BeautifulSoup(page.text, "html.parser")
    form = None

    for candidate in soup.find_all("form"):
        action = candidate.get("action", "")

        text_input = candidate.find(
            "input",
            type=lambda value: value and value.lower() in ("text", "url")
        )

        if "download" in action.lower() and text_input:
            form = candidate
            break

    if not form:
        raise RuntimeError("Download form not found")

    action = form.get("action", "")

    if not action:
        raise RuntimeError("Form action is missing")

    method = form.get("method", "get").lower()

    if method != "post":
        raise RuntimeError("Unexpected form method")

    download_endpoint = urljoin(page.url, action)

    payload = {}
    url_field_found = False

    for field in form.find_all("input"):
        name = field.get("name")
        field_type = field.get("type", "text").lower()

        if not name or field.has_attr("disabled"):
            continue

        if field_type in ("submit", "button", "reset", "file", "image"):
            continue

        if field_type in ("text", "url") and not url_field_found:
            payload[name] = tiktok_url
            url_field_found = True
        else:
            payload[name] = field.get("value", "")

    if not url_field_found:
        raise RuntimeError("TikTok URL field not found")

    if "verify" in payload and not payload["verify"]:
        payload["verify"] = "1"

    return download_endpoint, payload, page.url


def extract_video_link(response):
    soup = BeautifulSoup(response.text, "html.parser")
    candidates = []

    for link in soup.find_all("a", href=True):
        href = urljoin(response.url, link["href"])
        parsed = urlparse(href)

        if parsed.hostname != "fastdl.muscdn.app":
            continue

        if not parsed.path.startswith("/v3"):
            continue

        label = link.get_text(" ", strip=True).lower()

        if "mp4" not in label or "watermark" in label:
            continue

        priority = 0 if "hd" in label else 1
        candidates.append((priority, href))

    if not candidates:
        raise RuntimeError("MP4 download link not found")

    candidates.sort(key=lambda item: item[0])

    return candidates[0][1]


def get_output_path(number):
    output = OUTPUT_DIR / f"video_{number:03d}.mp4"
    counter = 1

    while output.exists():
        output = OUTPUT_DIR / f"video_{number:03d}_{counter}.mp4"
        counter += 1

    return output



def save_video(video_url, output_file, referer):
    temporary_file = output_file.with_name(
        output_file.name + ".part"
    )

    try:
        with session.get(
            video_url,
            headers={"Referer": referer},
            stream=True,
            timeout=(REQUEST_TIMEOUT, DOWNLOAD_TIMEOUT)
        ) as response:

            response.raise_for_status()

            content_length = response.headers.get("Content-Length")
            total_size = int(content_length) if content_length and content_length.isdigit() else None

            with open(temporary_file, "wb") as file:
                with tqdm(
                    total=total_size,
                    desc=output_file.name,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    dynamic_ncols=True
                ) as progress:

                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            file.write(chunk)
                            progress.update(len(chunk))

        if not temporary_file.exists() or temporary_file.stat().st_size == 0:
            raise RuntimeError("Downloaded file is empty")

        with open(temporary_file, "rb") as file:
            header = file.read(12)

        if len(header) < 8 or header[4:8] != b"ftyp":
            raise RuntimeError("Downloaded file is not a valid MP4")

        temporary_file.replace(output_file)

    except Exception:
        temporary_file.unlink(missing_ok=True)
        raise


    try:
        with session.get(
            video_url,
            headers={"Referer": referer},
            stream=True,
            timeout=(REQUEST_TIMEOUT, DOWNLOAD_TIMEOUT)
        ) as response:

            response.raise_for_status()

            with open(temporary_file, "wb") as file:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        file.write(chunk)

        if not temporary_file.exists() or temporary_file.stat().st_size == 0:
            raise RuntimeError("Downloaded file is empty")

        with open(temporary_file, "rb") as file:
            header = file.read(12)

        if len(header) < 8 or header[4:8] != b"ftyp":
            raise RuntimeError("Downloaded file is not a valid MP4")

        temporary_file.replace(output_file)

    except Exception:
        temporary_file.unlink(missing_ok=True)
        raise


def download_video(tiktok_url, number):
    print(f"\n[{number}] Processing...")

    try:
        endpoint, payload, referer = get_download_form(tiktok_url)

        response = session.post(
            endpoint,
            data=payload,
            headers={
                "Referer": referer,
                "Origin": BASE_URL
            },
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        video_url = extract_video_link(response)
        output_file = get_output_path(number)

        save_video(
            video_url,
            output_file,
            response.url
        )

        print(f"[SUCCESS] {output_file}")
        return True

    except requests.RequestException as error:
        print(f"[FAILED] Network error: {type(error).__name__}")

    except Exception as error:
        print(f"[FAILED] {error}")

    return False


def start_download(urls):
    total = len(urls)
    success = 0
    failed = 0

    print(f"\nStarting download: {total} videos")

    for index, url in enumerate(urls, start=1):
        print(f"\nProgress: {index}/{total}")

        if download_video(url, index):
            success += 1
        else:
            failed += 1

        if index < total:
            time.sleep(DELAY_BETWEEN_DOWNLOADS)

    print("\n========== DOWNLOAD REPORT ==========")
    print(f"Total   : {total}")
    print(f"Success : {success}")
    print(f"Failed  : {failed}")
    print(f"Folder  : {OUTPUT_DIR.resolve()}")
    print("=====================================")


def confirm_download(urls):
    if not urls:
        print("\nNo URLs available.")
        return

    while True:
        choice = input("\nStart download? [y/n]: ").strip().lower()

        if choice == "y":
            start_download(urls)
            break

        elif choice == "r":
            print("Returning to menu.")
            break

        else:
            print("Please enter y or n.")


def add_url():
    urls = []

    print("\nPaste TikTok URLs.")
    print("Press ENTER on an empty line to continue.\n")

    while True:
        url = input("> ").strip()

        if not url:
            break

        if not is_tiktok_url(url):
            print("Invalid TikTok URL.")
            continue

        if url in urls:
            print("URL already exists.")
            continue

        urls.append(url)
        print("Added.")

    confirm_download(urls)


def import_urls():
    filename = input("\nFile path [urls.txt]: ").strip()

    if not filename:
        filename = "urls.txt"

    path = Path(filename.strip('"'))

    if not path.is_file():
        print("File not found.")
        return

    urls = []

    try:
        with open(path, "r", encoding="utf-8-sig") as file:
            for line in file:
                url = line.strip()

                if not url or url.startswith("#"):
                    continue

                if not is_tiktok_url(url):
                    continue

                if url not in urls:
                    urls.append(url)

        print(f"\n{len(urls)} URLs imported.")

        confirm_download(urls)

    except OSError as error:
        print(f"Unable to read file: {error}")


def main():
    while True:
        print("\n")
        print("TIKTOK DOWNLOADER")
        print("-----------------")
        print("1. add url")
        print("2. import urls")
        print("3. exit")
        print("-----------------")

        choice = input("Select: ").strip()

        if choice == "1":
            add_url()

        elif choice == "2":
            import_urls()

        elif choice == "3":
            print("\nExiting...")
            break

        else:
            print("\nInvalid selection.")


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print("\n\nInterrupted.")

    finally:
        session.close()
