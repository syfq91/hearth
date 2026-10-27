"""In-process metadata and cover extractors for EPUB, CBZ, CBR, and PDF books."""

from __future__ import annotations

import contextlib
import io
import logging
import mimetypes
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pypdf

logger = logging.getLogger("hearth.media.books")

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


@dataclass
class BookTagData:
    """Metadata extracted from a book file."""

    title: str
    author: str | None = None
    series: str | None = None
    series_index: float | None = None
    isbn: str | None = None
    publisher: str | None = None
    published_date: str | None = None
    language: str | None = None
    overview: str | None = None
    page_count: int | None = None
    cover_data: bytes | None = None
    cover_mime: str | None = None


def natural_sort_key(s: str) -> list[int | str]:
    """Sort strings containing numbers in natural human order."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s)]


def _strip_ns(tag: str) -> str:
    """Strip XML namespace from a tag name."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def read_epub_metadata(path: Path) -> BookTagData:
    """Read metadata and cover image from an EPUB file via direct ZIP and XML parsing."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            # 1. Locate OPF file from META-INF/container.xml
            try:
                container_data = zf.read("META-INF/container.xml")
                c_root = ET.fromstring(container_data)
                opf_path: str | None = None
                for elem in c_root.iter():
                    if _strip_ns(elem.tag) == "rootfile":
                        media_type = elem.attrib.get("media-type", "")
                        if "oebps-package+xml" in media_type or media_type.endswith("xml"):
                            opf_path = elem.attrib.get("full-path")
                            break
                        if opf_path is None and "full-path" in elem.attrib:
                            opf_path = elem.attrib.get("full-path")
            except Exception as exc:
                logger.debug("Failed to read container.xml from %s: %s", path, exc)
                opf_path = None

            if not opf_path:
                # Search for any .opf file in the archive
                opf_candidates = [n for n in zf.namelist() if n.lower().endswith(".opf")]
                if opf_candidates:
                    opf_path = opf_candidates[0]
                else:
                    return BookTagData(title=path.stem)

            opf_dir = os.path.dirname(opf_path)
            opf_data = zf.read(opf_path)
            opf_root = ET.fromstring(opf_data)

            title: str | None = None
            author: str | None = None
            overview: str | None = None
            isbn: str | None = None
            publisher: str | None = None
            published_date: str | None = None
            language: str | None = None
            series: str | None = None
            series_index: float | None = None
            cover_item_id: str | None = None

            # 2. Parse metadata tags
            for elem in opf_root.iter():
                tag = _strip_ns(elem.tag).lower()
                text = (elem.text or "").strip()

                if tag == "title" and not title and text:
                    title = text
                elif tag == "creator" and not author and text:
                    author = text
                elif tag == "description" and not overview and text:
                    overview = text
                elif tag == "publisher" and not publisher and text:
                    publisher = text
                elif tag == "language" and not language and text:
                    language = text
                elif tag == "date" and not published_date and text:
                    published_date = text
                elif tag == "identifier" and text and not isbn:
                    scheme = elem.attrib.get("scheme", "").upper()
                    for k, v in elem.attrib.items():
                        if _strip_ns(k).lower() == "scheme":
                            scheme = v.upper()
                    id_attr = elem.attrib.get("id", "").lower()
                    digits = re.sub(r"[^0-9X]", "", text.upper())
                    if scheme == "ISBN" or "isbn" in id_attr or len(digits) in (10, 13):
                        isbn = text

                elif tag == "meta":
                    name = elem.attrib.get("name", "").lower()
                    prop = elem.attrib.get("property", "").lower()
                    content = elem.attrib.get("content", text).strip()

                    if name == "calibre:series" and content:
                        series = content
                    elif name == "calibre:series_index" and content:
                        with contextlib.suppress(ValueError):
                            series_index = float(content)
                    elif prop == "belongs-to-collection" and text:
                        if not series:
                            series = text
                    elif prop == "group-position" and text and series_index is None:
                        with contextlib.suppress(ValueError):
                            series_index = float(text)
                    elif name == "cover" and content:
                        cover_item_id = content

            # 3. Locate and extract cover image from manifest
            cover_href: str | None = None
            manifest_images: list[tuple[str, str, str]] = []  # (id, href, properties)

            for elem in opf_root.iter():
                if _strip_ns(elem.tag).lower() == "item":
                    i_id = elem.attrib.get("id", "")
                    href = elem.attrib.get("href", "")
                    m_type = elem.attrib.get("media-type", "").lower()
                    props = elem.attrib.get("properties", "").lower()

                    if m_type.startswith("image/"):
                        manifest_images.append((i_id, href, props))
                        is_match = bool(cover_item_id and i_id == cover_item_id)
                        is_hint = not cover_href and (
                            "cover-image" in props or "cover" in i_id.lower()
                        )
                        if is_match or is_hint:
                            cover_href = href

            if not cover_href and manifest_images:
                for _, href, _ in manifest_images:
                    if "cover" in href.lower():
                        cover_href = href
                        break

            cover_data: bytes | None = None
            cover_mime: str | None = None

            if cover_href:
                target_path = (
                    f"{opf_dir}/{cover_href}".lstrip("/") if opf_dir else cover_href.lstrip("/")
                )
                parts = target_path.split("/")
                clean_parts: list[str] = []
                for p in parts:
                    if p == "..":
                        if clean_parts:
                            clean_parts.pop()
                    elif p and p != ".":
                        clean_parts.append(p)
                normalized = "/".join(clean_parts)

                try:
                    cover_data = zf.read(normalized)
                    ext = os.path.splitext(normalized)[1].lower()
                    cover_mime = mimetypes.types_map.get(ext, "image/jpeg")
                except KeyError:
                    try:
                        cover_data = zf.read(cover_href)
                        ext = os.path.splitext(cover_href)[1].lower()
                        cover_mime = mimetypes.types_map.get(ext, "image/jpeg")
                    except KeyError:
                        cover_data = None

            return BookTagData(
                title=title or path.stem,
                author=author,
                series=series,
                series_index=series_index,
                isbn=isbn,
                publisher=publisher,
                published_date=published_date,
                language=language,
                overview=overview,
                cover_data=cover_data,
                cover_mime=cover_mime,
            )

    except Exception as exc:
        logger.warning("Error reading EPUB metadata from %s: %s", path, exc)
        return BookTagData(title=path.stem)


def read_cbz_metadata(path: Path) -> BookTagData:
    """Read metadata and cover from a Comic Book Zip (CBZ) archive."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            namelist = zf.namelist()
            images = [
                n
                for n in namelist
                if not n.startswith("__MACOSX/")
                and not os.path.basename(n).startswith(".")
                and os.path.splitext(n)[1].lower() in _IMAGE_EXTENSIONS
            ]
            images.sort(key=natural_sort_key)

            title: str | None = None
            series: str | None = None
            series_index: float | None = None
            author: str | None = None
            overview: str | None = None
            published_date: str | None = None
            page_count: int | None = len(images) if images else None

            comic_info_name = next(
                (n for n in namelist if os.path.basename(n).lower() == "comicinfo.xml"), None
            )
            if comic_info_name:
                try:
                    c_xml = zf.read(comic_info_name)
                    c_root = ET.fromstring(c_xml)
                    for elem in c_root:
                        tag = _strip_ns(elem.tag).lower()
                        text = (elem.text or "").strip()
                        if not text:
                            continue
                        if tag == "title":
                            title = text
                        elif tag == "series":
                            series = text
                        elif tag == "number":
                            with contextlib.suppress(ValueError):
                                series_index = float(text)
                        elif tag in ("writer", "author") and not author:
                            author = text
                        elif tag in ("summary", "overview") and not overview:
                            overview = text
                        elif tag == "year" and not published_date:
                            published_date = text
                        elif tag == "pagecount":
                            with contextlib.suppress(ValueError):
                                page_count = int(text)
                except Exception as exc:
                    logger.debug("Failed parsing ComicInfo.xml in %s: %s", path, exc)

            cover_data: bytes | None = None
            cover_mime: str | None = None
            if images:
                cover_entry = images[0]
                cover_data = zf.read(cover_entry)
                ext = os.path.splitext(cover_entry)[1].lower()
                cover_mime = mimetypes.types_map.get(ext, "image/jpeg")

            fallback_title = (
                f"{series} #{series_index}" if series and series_index is not None else path.stem
            )
            return BookTagData(
                title=title or fallback_title,
                author=author,
                series=series,
                series_index=series_index,
                overview=overview,
                published_date=published_date,
                page_count=page_count,
                cover_data=cover_data,
                cover_mime=cover_mime,
            )
    except Exception as exc:
        logger.warning("Error reading CBZ metadata from %s: %s", path, exc)
        return BookTagData(title=path.stem)


def extract_comic_page(path: Path, page_number: int) -> tuple[bytes, str]:
    """Extract the nth (1-indexed) image page from a CBZ or CBR archive."""
    if page_number < 1:
        raise IndexError(f"Page number must be >= 1, got {page_number}")

    suffix = path.suffix.lower()
    if suffix == ".cbz" or zipfile.is_zipfile(path):
        with zipfile.ZipFile(path, "r") as zf:
            images = [
                n
                for n in zf.namelist()
                if not n.startswith("__MACOSX/")
                and not os.path.basename(n).startswith(".")
                and os.path.splitext(n)[1].lower() in _IMAGE_EXTENSIONS
            ]
            images.sort(key=natural_sort_key)
            if page_number > len(images):
                raise IndexError(f"Page {page_number} out of bounds (1-{len(images)})")
            target = images[page_number - 1]
            data = zf.read(target)
            ext = os.path.splitext(target)[1].lower()
            mime = mimetypes.types_map.get(ext, "image/jpeg")
            return data, mime

    elif suffix == ".cbr":
        try:
            import rarfile

            with rarfile.RarFile(path, "r") as rf:
                images = [
                    n
                    for n in rf.namelist()
                    if not os.path.basename(n).startswith(".")
                    and os.path.splitext(n)[1].lower() in _IMAGE_EXTENSIONS
                ]
                images.sort(key=natural_sort_key)
                if page_number > len(images):
                    raise IndexError(f"Page {page_number} out of bounds (1-{len(images)})")
                target = images[page_number - 1]
                data = rf.read(target)
                ext = os.path.splitext(target)[1].lower()
                mime = mimetypes.types_map.get(ext, "image/jpeg")
                return data, mime
        except Exception as exc:
            raise RuntimeError(f"Failed to read CBR archive {path}: {exc}") from exc

    elif suffix == ".pdf":
        try:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(path))
            if page_number > len(pdf):
                raise IndexError(f"Page {page_number} out of bounds (1-{len(pdf)})")
            page = pdf.get_page(page_number - 1)
            pil_image = page.render(scale=2.0).to_pil()
            buf = io.BytesIO()
            pil_image.save(buf, format="JPEG")
            return buf.getvalue(), "image/jpeg"
        except IndexError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Failed to render PDF page {page_number} from {path}: {exc}"
            ) from exc

    raise ValueError(f"Unsupported comic container: {suffix}")


def read_pdf_metadata(path: Path) -> BookTagData:
    """Read metadata and render page 0 cover from a PDF file."""
    try:
        reader = pypdf.PdfReader(str(path))
        page_count = len(reader.pages)
        info = reader.metadata

        title: str | None = None
        author: str | None = None
        overview: str | None = None
        published_date: str | None = None

        if info:
            title = info.title
            author = info.author
            overview = info.subject
            if info.creation_date:
                with contextlib.suppress(Exception):
                    published_date = str(info.creation_date.year)

        cover_data: bytes | None = None
        cover_mime: str | None = None

        try:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(path))
            if len(pdf) > 0:
                page = pdf[0]
                pil_img = page.render(scale=2.0).to_pil()
                buf = io.BytesIO()
                pil_img.save(buf, format="JPEG", quality=85)
                cover_data = buf.getvalue()
                cover_mime = "image/jpeg"
        except Exception as exc:
            logger.debug("pypdfium2 failed to render cover for %s: %s", path, exc)

        return BookTagData(
            title=title or path.stem,
            author=author,
            overview=overview,
            published_date=published_date,
            page_count=page_count,
            cover_data=cover_data,
            cover_mime=cover_mime,
        )
    except Exception as exc:
        logger.warning("Error reading PDF metadata from %s: %s", path, exc)
        return BookTagData(title=path.stem)


def read_cbr_metadata(path: Path) -> BookTagData:
    """Read metadata and cover from a Comic Book RAR (CBR) archive."""
    try:
        import rarfile

        if rarfile.is_rarfile(str(path)):
            with rarfile.RarFile(str(path), "r") as rf:
                namelist = rf.namelist()
                images = [
                    n
                    for n in namelist
                    if not os.path.basename(n).startswith(".")
                    and os.path.splitext(n)[1].lower() in _IMAGE_EXTENSIONS
                ]
                images.sort(key=natural_sort_key)
                page_count = len(images) if images else None
                cover_data: bytes | None = None
                cover_mime: str | None = None
                if images:
                    cover_entry = images[0]
                    cover_data = rf.read(cover_entry)
                    ext = os.path.splitext(cover_entry)[1].lower()
                    cover_mime = mimetypes.types_map.get(ext, "image/jpeg")

                return BookTagData(
                    title=path.stem,
                    page_count=page_count,
                    cover_data=cover_data,
                    cover_mime=cover_mime,
                )
    except Exception as exc:
        logger.debug("Failed to read CBR metadata from %s: %s", path, exc)

    return BookTagData(title=path.stem)


def read_book_metadata(path: Path) -> BookTagData:
    """Dispatch book metadata reading based on file format extension."""
    suffix = path.suffix.lower()
    if suffix == ".epub":
        return read_epub_metadata(path)
    elif suffix == ".cbz":
        return read_cbz_metadata(path)
    elif suffix == ".cbr":
        return read_cbr_metadata(path)
    elif suffix == ".pdf":
        return read_pdf_metadata(path)
    else:
        # MOBI, AZW3 or other download-only formats
        return BookTagData(title=path.stem)
