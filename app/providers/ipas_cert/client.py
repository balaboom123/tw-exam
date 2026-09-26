from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata

USER_AGENT = "Mozilla/5.0 (compatible; ipas-cert-mirror/1.0)"
CANONICAL_CATEGORY = "iPAS產業人才能力鑑定官方下載"
MATERIALS_YEAR = 2026
CURRENT_BASE_URL = "https://ipd.nat.gov.tw/ipas/"
IPAS_HOSTS = ("www.ipas.org.tw", "ipd.nat.gov.tw")
IPAS_SECTIONS = ("news", "exam-info", "learning-resources", "downloads")
IPAS_IT_CERTS = {
    "ISE": "資訊安全工程師",
    "OIA": "營運智慧分析師",
    "AIAP": "AI應用規劃師",
    "AIOT": "AIoT應用工程師",
}


@dataclass(frozen=True)
class IpasDownload:
    cert_code: str
    label: str
    url: str


def _quote_url_for_request(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(unquote(parts.path), safe="/:%"),
            quote(unquote(parts.query), safe="=&:%"),
            quote(unquote(parts.fragment), safe=""),
        )
    )


def _normalize_ipas_url(url: str) -> str:
    ref = url.strip().replace("&amp;", "&")
    if ref.startswith("http"):
        parts = urlsplit(ref)
        path = parts.path
    else:
        path = ref if ref.startswith("/") else f"/{ref}"
        parts = urlsplit(CURRENT_BASE_URL)
    if path.startswith("/api/"):
        path = f"/ipas{path}"
    elif not path.startswith("/ipas/"):
        path = f"/ipas{path}"
    return _quote_url_for_request(urlunsplit(("https", "ipd.nat.gov.tw", path, parts.query, "")))


def _slug(text: str, fallback: str) -> str:
    ascii_slug = re.sub(r"[^0-9A-Za-z]+", "-", text).strip("-").lower()
    if ascii_slug:
        return ascii_slug
    encoded = text.encode("utf-8").hex()[:24]
    return encoded or fallback


def parse_certification_codes(html: str) -> list[str]:
    codes = set(re.findall(r"(?:/ipas)?/certification/([A-Z0-9]+)/news", html))
    return sorted(codes)


_IPAS_HOST_RE = "|".join(re.escape(host) for host in IPAS_HOSTS)
_PDF_REF_RE = re.compile(
    rf"(?:https://(?:{_IPAS_HOST_RE}))?(?:/ipas)?/api/proxy/uploads/[^\"'<>]+?\.pdf",
    re.IGNORECASE,
)


def parse_pdf_downloads(html: str, *, cert_code: str = "") -> list[IpasDownload]:
    downloads: list[IpasDownload] = []
    seen: set[str] = set()
    for ref in _PDF_REF_RE.findall(html):
        url = _normalize_ipas_url(ref)
        if url in seen:
            continue
        seen.add(url)
        label = Path(unquote(urlparse(url).path)).name
        downloads.append(IpasDownload(cert_code=cert_code, label=label, url=url))
    return downloads


class IpasCertClient:
    provider_id = "ipas_cert"
    HOME_URL = "https://www.ipas.org.tw/"

    def _fetch_text(self, url: str) -> str:
        request = Request(_quote_url_for_request(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            body: bytes = response.read()
            return body.decode("utf-8", "replace")

    def _downloads(self, cert_code: str) -> list[IpasDownload]:
        downloads: list[IpasDownload] = []
        seen: set[str] = set()
        for section in IPAS_SECTIONS:
            html = self._fetch_text(f"{CURRENT_BASE_URL}certification/{cert_code}/{section}")
            for download in parse_pdf_downloads(html, cert_code=cert_code):
                if download.url in seen:
                    continue
                seen.add(download.url)
                downloads.append(download)
        return downloads

    def discover_available_years(self) -> list[int]:
        return [MATERIALS_YEAR]

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        if year_ad != MATERIALS_YEAR:
            return []
        return [
            ExamOption(
                code=f"ipas-cert-{code.lower()}-{year_ad}",
                year_ad=year_ad,
                year_roc=year_ad - 1911,
                label=f"iPAS {name}",
            )
            for code, name in IPAS_IT_CERTS.items()
        ]

    def _cert_code_from_exam_code(self, exam_code: str) -> str:
        for code in IPAS_IT_CERTS:
            if exam_code.startswith(f"ipas-cert-{code.lower()}-"):
                return code
        return ""

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        cert_code = self._cert_code_from_exam_code(exam_code)
        downloads = self._downloads(cert_code) if cert_code else []
        papers = [
            ParsedPaper(
                category_raw=f"{CANONICAL_CATEGORY}_{download.cert_code}",
                category_code=download.cert_code.lower(),
                subject_code=_slug(download.label, f"download-{index}"),
                subject_name_raw=download.label,
                files={"question": download.url},
            )
            for index, download in enumerate(downloads, start=1)
        ]
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw=f"iPAS {IPAS_IT_CERTS.get(cert_code, '產業人才能力鑑定官方下載')}",
            attachments=[],
            papers=papers,
            provider_id=self.provider_id,
        )

    def head(self, url: str) -> ResponseMetadata:
        request = Request(
            _quote_url_for_request(url), headers={"User-Agent": USER_AGENT}, method="HEAD"
        )
        with urlopen(request, timeout=60) as response:
            content_length = response.headers.get("Content-Length")
            return ResponseMetadata(
                url=url,
                status=response.status,
                content_length=int(content_length) if content_length else None,
                content_type=response.headers.get("Content-Type", ""),
                content_disposition=response.headers.get("Content-Disposition", ""),
                cache_control=response.headers.get("Cache-Control", ""),
            )

    def download_file(self, url: str) -> DownloadedFile:
        request = Request(_quote_url_for_request(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=120) as response:
            return DownloadedFile(
                data=response.read(),
                content_type=response.headers.get("Content-Type", "application/octet-stream"),
                file_name=Path(unquote(urlparse(url).path)).name,
            )
