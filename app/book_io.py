from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET
import html
import re
import zipfile

from app.models import Book, Chapter, Paragraph, slugify


XHTML_NS = "http://www.w3.org/1999/xhtml"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"


def load_source_book(path: Path, title: str | None = None) -> Book:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return load_txt_book(path, title=title)
    if suffix == ".epub":
        return load_epub_book(path, title=title)
    raise ValueError("只支持 .txt 和 .epub 文件")


def load_txt_book(path: Path, title: str | None = None) -> Book:
    text = read_text_guessing_encoding(path)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [block.strip() for block in re.split(r"\n\s*\n+", normalized) if block.strip()]
    book_title = title or path.stem
    chapter = Chapter(id="c0001", title=book_title, index=1)
    for index, block in enumerate(blocks, start=1):
        chapter.paragraphs.append(
            Paragraph(
                id=f"c0001-p{index:05d}",
                chapter_id=chapter.id,
                index=index,
                source=block,
            )
        )
    return Book(
        id=slugify(book_title),
        title=book_title,
        source_type="txt",
        source_file=str(path),
        chapters=[chapter],
    )


def read_text_guessing_encoding(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def load_epub_book(path: Path, title: str | None = None) -> Book:
    with zipfile.ZipFile(path) as archive:
        opf_path = _find_opf_path(archive)
        opf_dir = str(Path(opf_path).parent)
        opf = ET.fromstring(archive.read(opf_path))
        metadata_title = _metadata_title(opf)
        book_title = title or metadata_title or path.stem
        manifest = _opf_manifest(opf)
        spine_ids = _opf_spine_ids(opf)
        chapters: list[Chapter] = []
        chapter_index = 1
        for item_id in spine_ids:
            href = manifest.get(item_id)
            if not href:
                continue
            chapter_path = _join_zip_path(opf_dir, href)
            if chapter_path not in archive.namelist():
                continue
            chapter = _parse_epub_chapter(archive.read(chapter_path), chapter_index, chapter_path)
            if chapter.paragraphs:
                chapters.append(chapter)
                chapter_index += 1
    return Book(
        id=slugify(book_title),
        title=book_title,
        source_type="epub",
        source_file=str(path),
        chapters=chapters,
    )


def _find_opf_path(archive: zipfile.ZipFile) -> str:
    container = ET.fromstring(archive.read("META-INF/container.xml"))
    rootfile = container.find(f".//{{{CONTAINER_NS}}}rootfile")
    if rootfile is None:
        raise ValueError("EPUB 缺少 rootfile")
    full_path = rootfile.attrib.get("full-path")
    if not full_path:
        raise ValueError("EPUB rootfile 缺少 full-path")
    return full_path


def _metadata_title(opf: ET.Element) -> str:
    for element in opf.iter():
        if element.tag.endswith("}title") and element.text:
            return element.text.strip()
    return ""


def _opf_manifest(opf: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in opf.findall(f".//{{{OPF_NS}}}manifest/{{{OPF_NS}}}item"):
        item_id = item.attrib.get("id")
        href = item.attrib.get("href")
        media_type = item.attrib.get("media-type", "")
        if item_id and href and ("xhtml" in media_type or "html" in media_type):
            result[item_id] = href
    return result


def _opf_spine_ids(opf: ET.Element) -> list[str]:
    return [
        item.attrib["idref"]
        for item in opf.findall(f".//{{{OPF_NS}}}spine/{{{OPF_NS}}}itemref")
        if item.attrib.get("idref")
    ]


def _join_zip_path(base: str, href: str) -> str:
    if not base or base == ".":
        return href
    return str(Path(base) / href).replace("\\", "/")


def _parse_epub_chapter(data: bytes, index: int, source_path: str) -> Chapter:
    root = ET.fromstring(data)
    title = _chapter_title(root) or f"Chapter {index}"
    chapter = Chapter(id=f"c{index:04d}", title=title, index=index, source_path=source_path)
    paragraph_index = 1
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1].lower()
        if local_name not in {"p", "li", "blockquote", "h1", "h2", "h3", "h4"}:
            continue
        text = _element_text(element)
        if not text:
            continue
        chapter.paragraphs.append(
            Paragraph(
                id=f"{chapter.id}-p{paragraph_index:05d}",
                chapter_id=chapter.id,
                index=paragraph_index,
                source=text,
            )
        )
        paragraph_index += 1
    return chapter


def _chapter_title(root: ET.Element) -> str:
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1].lower()
        if local_name in {"h1", "h2", "title"}:
            text = _element_text(element)
            if text:
                return text
    return ""


def _element_text(element: ET.Element) -> str:
    text = "".join(element.itertext())
    text = html.unescape(text)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def export_txt(book: Book, output: Path, bilingual: bool = False) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = [book.title, ""]
    for chapter in book.chapters:
        chunks.extend([chapter.title, ""])
        for paragraph in chapter.paragraphs:
            text = paragraph.translated or paragraph.source
            if bilingual and paragraph.translated:
                chunks.append(paragraph.source)
            chunks.append(text)
            chunks.append("")
    output.write_text("\n".join(chunks).rstrip() + "\n", encoding="utf-8")


def export_epub(book: Book, output: Path) -> None:
    if book.source_type != "epub":
        raise ValueError("当前书籍不是 EPUB，无法导出 EPUB")
    output.parent.mkdir(parents=True, exist_ok=True)
    replacements = {
        chapter.source_path: {paragraph.source: paragraph.translated for paragraph in chapter.paragraphs if paragraph.translated}
        for chapter in book.chapters
    }
    source = Path(book.source_file)
    with zipfile.ZipFile(source, "r") as src, zipfile.ZipFile(output, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename in replacements:
                data = _replace_xhtml_text(data, replacements[info.filename])
            dst.writestr(info, data)


def _replace_xhtml_text(data: bytes, replacements: dict[str, str]) -> bytes:
    if not replacements:
        return data
    root = ET.fromstring(data)
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1].lower()
        if local_name not in {"p", "li", "blockquote", "h1", "h2", "h3", "h4"}:
            continue
        text = _element_text(element)
        translated = replacements.get(text)
        if translated:
            element.clear()
            element.text = translated
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)

