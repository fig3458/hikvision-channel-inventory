# AIGC START
"""Hikvision multi-NVR channel inventory: list, duplicate IP check, IP-table compare."""
from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPBasicAuthHandler,
    HTTPDigestAuthHandler,
    HTTPPasswordMgrWithDefaultRealm,
    Request,
    build_opener,
)

IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

LogFn = Callable[[str], None]


@dataclass
class LiveChannel:
    channel_id: int
    ip: str
    name: str
    protocol: str = ""
    manage_port: str = ""
    src_input_port: str = ""
    stream_type: str = ""
    user_name: str = ""
    addressing: str = ""
    nvr_url: str = ""
    nvr_label: str = ""
    online: bool | None = None  # None = 鏈娴?/ 鏈煡
    online_detail: str = ""

    @property
    def nvr_display(self) -> str:
        return self.nvr_label or self.nvr_url or "-"

    @property
    def online_label(self) -> str:
        if self.online is True:
            return "鍦ㄧ嚎"
        if self.online is False:
            return "绂荤嚎"
        return "鏈煡"

    def channel_label(self) -> str:
        return f"{self.nvr_display}/D{self.channel_id}"


@dataclass
class NvrOptions:
    base_url: str
    nvr_user: str
    nvr_password: str
    timeout: float = 25.0
    label: str = ""


@dataclass
class ParseNvrResult:
    items: list[NvrOptions]
    skipped: list[str]
    warnings: list[str]


@dataclass
class IpRow:
    ip: str
    name: str = ""
    note: str = ""
    raw_line: str = ""


@dataclass
class ParseIpResult:
    items: list[IpRow]
    skipped: list[str]
    warnings: list[str]


@dataclass
class DuplicateGroup:
    ip: str
    channels: list[LiveChannel]


@dataclass
class NvrFetchResult:
    opt: NvrOptions
    ok: bool
    device_name: str = ""
    channels: list[LiveChannel] = field(default_factory=list)
    error: str = ""
    status_checked: bool = False
    online_count: int = 0
    offline_count: int = 0


@dataclass
class InventoryReport:
    """Merged inventory across one or more NVRs."""
    device_name: str  # summary text
    channels: list[LiveChannel]
    total: int
    with_ip: int
    empty_ip: int
    unique_ips: int
    duplicates: list[DuplicateGroup]
    nvr_ok: int = 0
    nvr_fail: int = 0
    online_count: int = 0
    offline_count: int = 0
    unknown_online_count: int = 0
    checked_online: bool = False
    per_nvr: list[NvrFetchResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class CompareRow:
    ip: str
    name: str
    status: str  # 宸插瓨鍦?/ 鏈坊鍔?/ 琛ㄤ腑閲嶅
    channels: str
    nvr_names: str


@dataclass
class CompareReport:
    rows: list[CompareRow]
    exist_count: int
    missing_count: int
    table_dup_count: int
    orphan_on_nvr: list[LiveChannel]


def normalize_base_url(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("褰曞儚鏈哄湴鍧€涓嶈兘涓虹┖")
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    if not parsed.hostname:
        raise ValueError("褰曞儚鏈哄湴鍧€鏃犳晥")
    base = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        base += f":{parsed.port}"
    return base


def nvr_ip_label(base_url: str) -> str:
    """Stable short name from NVR host, e.g. 192.168.23.252 鈫?252褰曞儚鏈?"""
    host = urlparse(base_url).hostname if "://" in (base_url or "") else (base_url or "")
    host = (host or "").strip()
    if IP_RE.fullmatch(host):
        return f"{host.rsplit('.', 1)[-1]}褰曞儚鏈?
    return host or base_url or "褰曞儚鏈?


def build_camera_web_url(
    ip: str,
    user: str = "",
    password: str = "",
    embed_auth: bool = False,
    http_port: int = 80,
) -> str:
    """Camera web UI URL. embed_auth may auto-fill Basic/Digest on some browsers."""
    if not IP_RE.fullmatch(ip or ""):
        raise ValueError(f"鏃犳晥 IP锛歿ip}")
    host = ip if http_port == 80 else f"{ip}:{http_port}"
    if embed_auth and user:
        # browsers may strip credentials; still best-effort for digest/basic
        from urllib.parse import quote

        u = quote(user, safe="")
        p = quote(password or "", safe="")
        return f"http://{u}:{p}@{host}/"
    return f"http://{host}/"


def open_web_url(url: str) -> None:
    import webbrowser

    webbrowser.open(url, new=2)


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _find_text(el: ET.Element, names: set[str]) -> str:
    want = {n.lower() for n in names}
    for node in el.iter():
        if _local(node.tag).lower() in want and node.text and node.text.strip():
            return node.text.strip()
    return ""


def _child_text(el: ET.Element, name: str) -> str:
    want = name.lower()
    for child in list(el):
        if _local(child.tag).lower() == want and child.text and child.text.strip():
            return child.text.strip()
    return ""


def _split_row(line: str) -> list[str]:
    if "\t" in line:
        return [c.strip() for c in line.split("\t")]
    if "," in line and line.count(",") >= 1:
        try:
            row = next(csv.reader(io.StringIO(line)))
            return [c.strip() for c in row]
        except csv.Error:
            pass
    return [c.strip() for c in re.split(r"\s{2,}", line)]


def parse_nvr_list(
    text: str,
    default_user: str,
    default_password: str,
    timeout: float = 25.0,
) -> ParseNvrResult:
    """Parse NVR targets: one per line.

    Formats:
      http://192.168.1.10
      192.168.1.10
      192.168.1.10\\t澶囨敞鍚?
      192.168.1.10\\tadmin\\t瀵嗙爜
      192.168.1.10\\tadmin\\t瀵嗙爜\\t澶囨敞鍚?
    """
    items: list[NvrOptions] = []
    skipped: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if low.startswith("鍦板潃") or low.startswith("url") or low.startswith("褰曞儚鏈?):
            continue

        cols = [c for c in _split_row(line) if c != ""]
        if not cols:
            continue

        addr = cols[0]
        try:
            base = normalize_base_url(addr)
        except ValueError:
            skipped.append(f"鍦板潃鏃犳晥锛岃烦杩囷細{line[:80]}")
            continue

        user = default_user
        password = default_password
        label = ""

        if len(cols) == 2:
            # address + label  OR  address + user (if looks like account)
            if "@" in cols[1] or cols[1].lower() in {"admin", "user"}:
                user = cols[1]
            else:
                label = cols[1]
        elif len(cols) == 3:
            user, password = cols[1], cols[2]
        elif len(cols) >= 4:
            user, password, label = cols[1], cols[2], cols[3]

        if not user or not password:
            skipped.append(f"缂虹敤鎴峰悕/瀵嗙爜锛岃烦杩囷細{base}")
            continue
        if base in seen:
            warnings.append(f"褰曞儚鏈哄湴鍧€閲嶅锛屽凡蹇界暐锛歿base}")
            continue
        seen.add(base)
        if not label:
            label = nvr_ip_label(base)
        items.append(
            NvrOptions(
                base_url=base,
                nvr_user=user,
                nvr_password=password,
                timeout=timeout,
                label=label,
            )
        )

    return ParseNvrResult(items=items, skipped=skipped, warnings=warnings)


def _parse_bool_flag(text: str) -> bool | None:
    t = (text or "").strip().lower()
    if t in {"true", "1", "yes", "online", "connected", "ok", "normal"}:
        return True
    if t in {"false", "0", "no", "offline", "disconnected", "error", "fail"}:
        return False
    return None


def parse_channel_list(
    xml_text: str,
    nvr_url: str = "",
    nvr_label: str = "",
) -> dict[int, LiveChannel]:
    root = ET.fromstring(xml_text)
    channels: dict[int, LiveChannel] = {}
    nodes = [root] if _local(root.tag).lower() == "inputproxychannel" else []
    if not nodes:
        nodes = [
            el
            for el in root.iter()
            if _local(el.tag).lower() == "inputproxychannel"
        ]
    for el in nodes:
        cid_text = _find_text(el, {"id"})
        if not cid_text.isdigit():
            continue
        cid = int(cid_text)
        ip = _find_text(el, {"ipaddress", "ipv4address", "hostName", "hostname"})
        name = _child_text(el, "name") or _find_text(el, {"name"})
        protocol = _find_text(el, {"proxyprotocol", "protocol"})
        manage_port = _find_text(el, {"manageportno", "manageport"})
        src_input = _find_text(el, {"srcinputport"})
        stream_type = _find_text(el, {"streamtype"})
        user_name = _find_text(el, {"username"})
        addressing = _find_text(el, {"addressingformattype"})
        channels[cid] = LiveChannel(
            channel_id=cid,
            ip=ip,
            name=name,
            protocol=protocol,
            manage_port=manage_port,
            src_input_port=src_input,
            stream_type=stream_type,
            user_name=user_name,
            addressing=addressing,
            nvr_url=nvr_url,
            nvr_label=nvr_label,
        )
    return channels


def parse_channel_status_list(xml_text: str) -> dict[int, tuple[bool | None, str]]:
    """Parse GET /ISAPI/ContentMgmt/InputProxy/channels/status 鈫?{id: (online, detail)}."""
    root = ET.fromstring(xml_text)
    out: dict[int, tuple[bool | None, str]] = {}
    nodes = [
        el
        for el in root.iter()
        if _local(el.tag).lower() == "inputproxychannelstatus"
    ]
    if not nodes and _local(root.tag).lower() == "inputproxychannelstatus":
        nodes = [root]
    for el in nodes:
        cid_text = _child_text(el, "id") or _find_text(el, {"id"})
        if not cid_text.isdigit():
            continue
        cid = int(cid_text)
        online_raw = (
            _child_text(el, "online")
            or _find_text(el, {"online", "connstatus", "connectionstate", "chanstatus"})
        )
        online = _parse_bool_flag(online_raw)
        detect = _find_text(el, {"chandetectresult", "detectresult", "errordescription"})
        stream = _find_text(el, {"streamingchannelstatus", "streamstatus"})
        parts = []
        if online_raw:
            parts.append(f"online={online_raw}")
        if detect:
            parts.append(detect)
        if stream and stream.lower() not in {"true", "false"}:
            parts.append(stream)
        # Some firmwares omit <online> but put result in chanDetectResult
        if online is None and detect:
            low = detect.lower()
            if any(k in low for k in ("ok", "normal", "success", "connected")):
                online = True
            elif any(k in low for k in ("fail", "error", "offline", "timeout", "disconnect")):
                online = False
        out[cid] = (online, " ".join(parts).strip())
    return out


def parse_ip_table(text: str) -> ParseIpResult:
    """Parse Excel paste / CSV: camera IP + optional name / note."""
    items: list[IpRow] = []
    skipped: list[str] = []
    warnings: list[str] = []
    seen: dict[str, int] = {}

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("鐩戞帶") or line.startswith("IP") or line.startswith("閫氶亾"):
            continue
        if line.lower().startswith("ip"):
            continue

        cols = [c for c in _split_row(line) if c != ""]
        if not cols:
            continue

        ip = ""
        extras: list[str] = []
        for col in cols:
            if IP_RE.fullmatch(col) and not ip:
                ip = col
            else:
                extras.append(col)

        if not ip:
            skipped.append(f"鏃?IP锛岃烦杩囷細{line[:80]}")
            continue

        name = extras[0] if extras else ""
        note = " ".join(extras[1:]) if len(extras) > 1 else ""
        if ip in seen:
            warnings.append(f"IP 琛ㄥ唴閲嶅锛歿ip}锛堢 {seen[ip]} 琛屼笌鍚庣画琛岋級")
        else:
            seen[ip] = len(items) + 1
        items.append(IpRow(ip=ip, name=name, note=note, raw_line=line))

    return ParseIpResult(items=items, skipped=skipped, warnings=warnings)


def _ip_sort_key(ip: str) -> tuple:
    try:
        return tuple(int(p) for p in ip.split("."))
    except ValueError:
        return (999, 999, 999, 999)


def build_inventory_from_channels(
    channels: list[LiveChannel],
    device_name: str = "",
    per_nvr: list[NvrFetchResult] | None = None,
) -> InventoryReport:
    ordered = sorted(
        channels,
        key=lambda c: (c.nvr_url, c.channel_id),
    )
    by_ip: dict[str, list[LiveChannel]] = defaultdict(list)
    empty_ip = 0
    for ch in ordered:
        if ch.ip and IP_RE.fullmatch(ch.ip):
            by_ip[ch.ip].append(ch)
        else:
            empty_ip += 1

    duplicates = [
        DuplicateGroup(ip=ip, channels=chs)
        for ip, chs in sorted(by_ip.items(), key=lambda x: _ip_sort_key(x[0]))
        if len(chs) > 1
    ]
    notes: list[str] = []
    if duplicates:
        notes.append(f"鍙戠幇 {len(duplicates)} 涓噸澶?IP锛堝惈璺ㄥ綍鍍忔満锛?)
    if empty_ip:
        notes.append(f"{empty_ip} 璺棤鏈夋晥 IP")

    online_n = sum(1 for c in ordered if c.online is True)
    offline_n = sum(1 for c in ordered if c.online is False)
    unknown_n = sum(1 for c in ordered if c.online is None)
    checked = any(r.status_checked for r in (per_nvr or [])) or any(
        c.online is not None for c in ordered
    )
    if offline_n:
        notes.append(f"{offline_n} 璺绾?)

    results = per_nvr or []
    nvr_ok = sum(1 for r in results if r.ok)
    nvr_fail = sum(1 for r in results if not r.ok)
    if not device_name:
        if nvr_ok == 1 and results:
            ok_one = next(r for r in results if r.ok)
            device_name = ok_one.device_name or ok_one.opt.label
        else:
            device_name = f"{nvr_ok} 鍙板綍鍍忔満"

    return InventoryReport(
        device_name=device_name,
        channels=ordered,
        total=len(ordered),
        with_ip=len(ordered) - empty_ip,
        empty_ip=empty_ip,
        unique_ips=len(by_ip),
        duplicates=duplicates,
        nvr_ok=nvr_ok,
        nvr_fail=nvr_fail,
        online_count=online_n,
        offline_count=offline_n,
        unknown_online_count=unknown_n,
        checked_online=checked,
        per_nvr=results,
        notes=notes,
    )


def build_inventory(device_name: str, channels: dict[int, LiveChannel]) -> InventoryReport:
    """Single-NVR helper (kept for compatibility)."""
    return build_inventory_from_channels(list(channels.values()), device_name=device_name)


def compare_ip_table(
    table: list[IpRow],
    channels: list[LiveChannel] | dict[int, LiveChannel],
) -> CompareReport:
    if isinstance(channels, dict):
        ch_list = list(channels.values())
    else:
        ch_list = channels

    by_ip: dict[str, list[LiveChannel]] = defaultdict(list)
    for ch in ch_list:
        if ch.ip and IP_RE.fullmatch(ch.ip):
            by_ip[ch.ip].append(ch)

    seen_in_table: set[str] = set()
    rows: list[CompareRow] = []
    exist = missing = table_dup = 0

    def _ch_text(live: list[LiveChannel]) -> str:
        return "銆?.join(c.channel_label() for c in live) or "-"

    def _name_text(live: list[LiveChannel]) -> str:
        return "銆?.join(c.name for c in live if c.name) or "-"

    for item in table:
        if item.ip in seen_in_table:
            table_dup += 1
            live = by_ip.get(item.ip, [])
            rows.append(
                CompareRow(
                    ip=item.ip,
                    name=item.name,
                    status="琛ㄤ腑閲嶅",
                    channels=_ch_text(live),
                    nvr_names=_name_text(live),
                )
            )
            continue
        seen_in_table.add(item.ip)
        live = by_ip.get(item.ip, [])
        if live:
            exist += 1
            status = "宸插瓨鍦?
        else:
            missing += 1
            status = "鏈坊鍔?
        rows.append(
            CompareRow(
                ip=item.ip,
                name=item.name,
                status=status,
                channels=_ch_text(live),
                nvr_names=_name_text(live),
            )
        )

    orphan = [
        ch
        for ip, chs in by_ip.items()
        if ip not in seen_in_table
        for ch in sorted(chs, key=lambda c: (c.nvr_url, c.channel_id))
    ]
    return CompareReport(
        rows=rows,
        exist_count=exist,
        missing_count=missing,
        table_dup_count=table_dup,
        orphan_on_nvr=orphan,
    )


def export_channels_tsv(channels: list[LiveChannel]) -> str:
    lines = ["褰曞儚鏈篭t鍦板潃\t閫氶亾\t鍦ㄧ嚎\tIP\t鍚嶇О\t鍗忚\t绠＄悊绔彛\t婧愰€氶亾\t鐮佹祦\t鐢ㄦ埛鍚峔t鍦ㄧ嚎璇︽儏"]
    for ch in channels:
        lines.append(
            "\t".join(
                [
                    ch.nvr_label or "",
                    ch.nvr_url or "",
                    f"D{ch.channel_id}",
                    ch.online_label,
                    ch.ip or "",
                    ch.name or "",
                    ch.protocol or "",
                    ch.manage_port or "",
                    ch.src_input_port or "",
                    ch.stream_type or "",
                    ch.user_name or "",
                    ch.online_detail or "",
                ]
            )
        )
    return "\n".join(lines) + "\n"


def export_compare_tsv(report: CompareReport) -> str:
    lines = ["鐘舵€乗tIP\t琛ㄤ腑鍚嶇О\t鎵€鍦ㄩ€氶亾\t閫氶亾鍚嶇О"]
    for r in report.rows:
        lines.append(
            "\t".join([r.status, r.ip, r.name or "", r.channels, r.nvr_names])
        )
    if report.orphan_on_nvr:
        lines.append("")
        lines.append("# 褰曞儚鏈烘湁銆佽〃涓病鏈?)
        for ch in report.orphan_on_nvr:
            lines.append(
                "\t".join(
                    [
                        "浠呭綍鍍忔満",
                        ch.ip,
                        "",
                        ch.channel_label(),
                        ch.name or "",
                    ]
                )
            )
    return "\n".join(lines) + "\n"


class NvrClient:
    def __init__(self, opt: NvrOptions):
        self.opt = opt
        self.base_url = opt.base_url.rstrip("/")
        passman = HTTPPasswordMgrWithDefaultRealm()
        passman.add_password(None, self.base_url, opt.nvr_user, opt.nvr_password)
        self.opener = build_opener(
            HTTPDigestAuthHandler(passman),
            HTTPBasicAuthHandler(passman),
        )

    def request(self, method: str, path: str, body: str | None = None) -> tuple[int, str]:
        url = self.base_url + path
        data = body.encode("utf-8") if body is not None else None
        headers = {
            "Accept": "application/xml, text/xml, */*",
            "User-Agent": "HikvisionChannelInventory/1.1",
        }
        if body is not None:
            headers["Content-Type"] = 'application/xml; charset="UTF-8"'
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=self.opt.timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                return getattr(resp, "status", 200) or 200, text
        except HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            return exc.code, text
        except URLError as exc:
            raise ConnectionError(
                str(exc.reason if hasattr(exc, "reason") else exc)
            ) from exc

    def ping(self) -> str:
        code, text = self.request("GET", "/ISAPI/System/deviceInfo")
        if code >= 400:
            raise RuntimeError(f"鐧诲綍褰曞儚鏈哄け璐?HTTP {code}锛歿text[:200]}")
        try:
            root = ET.fromstring(text)
            name = _find_text(root, {"deviceName", "model"})
        except ET.ParseError:
            name = ""
        return name or "OK"

    def list_channels(self) -> dict[int, LiveChannel]:
        code, text = self.request("GET", "/ISAPI/ContentMgmt/InputProxy/channels")
        if code >= 400:
            raise RuntimeError(f"璇诲彇閫氶亾鍒楄〃澶辫触 HTTP {code}锛歿text[:200]}")
        label = self.opt.label or nvr_ip_label(self.base_url)
        return parse_channel_list(text, nvr_url=self.base_url, nvr_label=label)

    def list_channel_status(self) -> dict[int, tuple[bool | None, str]]:
        """NVR-reported camera online status for digital channels."""
        code, text = self.request(
            "GET", "/ISAPI/ContentMgmt/InputProxy/channels/status"
        )
        if code >= 400:
            raise RuntimeError(f"璇诲彇閫氶亾鍦ㄧ嚎鐘舵€佸け璐?HTTP {code}锛歿text[:200]}")
        return parse_channel_status_list(text)

    def apply_online_status(self, channels: dict[int, LiveChannel]) -> tuple[int, int]:
        """Fill online flags; return (online_count, offline_count)."""
        status = self.list_channel_status()
        online_n = offline_n = 0
        for cid, ch in channels.items():
            if cid not in status:
                continue
            flag, detail = status[cid]
            ch.online = flag
            ch.online_detail = detail
            if flag is True:
                online_n += 1
            elif flag is False:
                offline_n += 1
        return online_n, offline_n

    def fetch_inventory(self, check_online: bool = True) -> InventoryReport:
        name = self.ping()
        channels = self.list_channels()
        label = self.opt.label or nvr_ip_label(self.base_url)
        status_checked = False
        online_n = offline_n = 0
        for ch in channels.values():
            ch.nvr_label = label
            ch.nvr_url = self.base_url
        if check_online and channels:
            try:
                online_n, offline_n = self.apply_online_status(channels)
                status_checked = True
            except Exception:
                status_checked = False
        result = NvrFetchResult(
            opt=self.opt,
            ok=True,
            device_name=name,
            channels=list(channels.values()),
            status_checked=status_checked,
            online_count=online_n,
            offline_count=offline_n,
        )
        return build_inventory_from_channels(
            list(channels.values()),
            device_name=name,
            per_nvr=[result],
        )


def _fetch_one(opt: NvrOptions, check_online: bool = True) -> NvrFetchResult:
    try:
        client = NvrClient(opt)
        name = client.ping()
        channels = client.list_channels()
        label = opt.label or nvr_ip_label(opt.base_url)
        for ch in channels.values():
            ch.nvr_label = label
            ch.nvr_url = opt.base_url
        status_checked = False
        online_n = offline_n = 0
        if check_online and channels:
            try:
                online_n, offline_n = client.apply_online_status(channels)
                status_checked = True
            except Exception as exc:  # noqa: BLE001
                # channel list still usable; mark status unknown
                for ch in channels.values():
                    if ch.online_detail == "":
                        ch.online_detail = f"鐘舵€佹帴鍙ｅけ璐ワ細{exc}"
                status_checked = False
        return NvrFetchResult(
            opt=opt,
            ok=True,
            device_name=name,
            channels=list(channels.values()),
            status_checked=status_checked,
            online_count=online_n,
            offline_count=offline_n,
        )
    except Exception as exc:  # noqa: BLE001
        return NvrFetchResult(opt=opt, ok=False, error=str(exc))


def fetch_multi_inventory(
    targets: list[NvrOptions],
    on_log: LogFn | None = None,
    max_workers: int = 6,
    check_online: bool = True,
) -> InventoryReport:
    """Fetch channels from many NVRs in parallel; merge + cross-NVR duplicate check."""
    log = on_log or (lambda _m: None)
    if not targets:
        raise ValueError("娌℃湁鍙媺鍙栫殑褰曞儚鏈?)

    results: list[NvrFetchResult] = []
    workers = max(1, min(max_workers, len(targets)))
    tip = "骞舵娴嬫憚鍍忓ご鍦ㄧ嚎鐘舵€? if check_online else "涓嶆娴嬪湪绾?
    log(f"寮€濮嬫媺鍙?{len(targets)} 鍙板綍鍍忔満锛堝苟琛?{workers}锛寋tip}锛夆€?)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(_fetch_one, opt, check_online): opt for opt in targets
        }
        for fut in as_completed(futs):
            opt = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                res = NvrFetchResult(opt=opt, ok=False, error=str(exc))
            results.append(res)
            tag = res.opt.label or res.opt.base_url
            if res.ok:
                extra = ""
                if check_online:
                    if res.status_checked:
                        extra = f" 路 鍦ㄧ嚎 {res.online_count} / 绂荤嚎 {res.offline_count}"
                    else:
                        extra = " 路 鍦ㄧ嚎鐘舵€佹湭鍙栧埌"
                log(f"鉁?{tag}锛坽res.device_name}锛壜?{len(res.channels)} 璺瘂extra}")
            else:
                log(f"鉁?{tag} 澶辫触锛歿res.error}")

    # stable order: original target order
    by_url = {r.opt.base_url: r for r in results}
    ordered_results = [by_url[t.base_url] for t in targets if t.base_url in by_url]
    all_channels: list[LiveChannel] = []
    for r in ordered_results:
        all_channels.extend(r.channels)

    report = build_inventory_from_channels(all_channels, per_nvr=ordered_results)
    log(
        f"鍚堣锛氭垚鍔?{report.nvr_ok} 鍙?/ 澶辫触 {report.nvr_fail} 鍙?路 "
        f"鐩戞帶 {report.total} 璺?路 鍞竴 IP {report.unique_ips} 路 "
        f"閲嶅 IP {len(report.duplicates)} 缁?
        + (
            f" 路 鍦ㄧ嚎 {report.online_count} / 绂荤嚎 {report.offline_count}"
            if check_online
            else ""
        )
    )
    return report


# AIGC END

