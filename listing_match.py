#!/usr/bin/env python3
"""네이버 광고를 실물 매물 후보로 묶고 원부동산 매물장과 대조한다.

흐름
1. 개별 광고(중복처리 전)를 같은 실물로 보이는 묶음으로 합친다.
   - 같은 단지·동·평형(±2평)
   - 층이 호환(정확층끼리는 같아야 하고, 저/중/고 표기는 해당 구간에 포함)
   - 가격 차이가 1억 또는 2% 이내
   - 한 묶음 안에는 서로 다른 정확층이 섞이지 않는다
2. 2곳 이상의 중개업소가 광고한 묶음을 '진성'으로 본다.
3. 매물장 '매매물건 목록'과 단지·동·평형·층·가격으로 대조해 등록 여부를 판정한다.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Iterable

from drive_archive import ZONE_BY_COMPLEX, nominal_pyeong, parse_price

# 비교 단지는 매물장 대조 대상에서 제외한다.
EXCLUDED_COMPLEXES = {"메이플자이", "트리마제", "한남더힐", "압구정하이츠파크"}

# 매물장 단지명 → 네이버 단지명
SOURCE_COMPLEX_ALIASES = {
    "현대10차": "현대10,13,14차",
    "현대13차": "현대10,13,14차",
    "현대14차": "현대10,13,14차",
    "대림아크로빌": "현대65동(대림아크로빌)",
    "현대65동": "현대65동(대림아크로빌)",
    "한양1차": "영동한양1차",
    "메이플": "메이플자이",
}

MIN_BROKERS_FOR_GENUINE = 2
PYEONG_TOLERANCE = 2
ALIGN_SINGLE_TYPE_TOLERANCE = 5
CLUSTER_PRICE_TOLERANCE_MAN = 10000      # 같은 실물로 묶을 가격 차이(1억)
CLUSTER_PRICE_TOLERANCE_RATIO = 0.02
REGISTER_PRICE_TOLERANCE_MAN = 20000     # 저/중/고 표기일 때 매물장 가격 근접 판단(2억)
REGISTER_PRICE_TOLERANCE_RATIO = 0.03

STATUS_UNREGISTERED = "🔴미등록"
STATUS_PRICE_DIFF = "🟠유사·가격상이"
STATUS_REGISTERED = "✅등록"
STATUS_INACTIVE = "⚪거래완료·보류"

STATUS_ORDER = {
    STATUS_UNREGISTERED: 0,
    STATUS_PRICE_DIFF: 1,
    STATUS_INACTIVE: 2,
    STATUS_REGISTERED: 3,
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _dong_no(value: str) -> str:
    text = re.sub(r"\s+", "", _text(value))
    text = re.sub(r"동$", "", text)
    match = re.fullmatch(r"0*(\d+)", text)
    return match.group(1) if match else text


def _pyeong_no(value: str) -> int | None:
    match = re.search(r"(\d+)", _text(value))
    return int(match.group(1)) if match else None


def band_of(floor: int, total: int | None) -> str:
    """정확층을 저/중/고 구간으로 바꾼다. 총층을 모르면 15층 기준."""
    total = total or 15
    if floor <= max(1, total // 3):
        return "저"
    if floor > (total * 2) // 3:
        return "고"
    return "중"


@dataclass(frozen=True)
class Floor:
    exact: int | None
    band: str            # 저/중/고 또는 ""
    total: int | None

    def label(self) -> str:
        total = f"/{self.total}" if self.total else ""
        if self.exact is not None:
            return f"{self.exact}층{total}"
        return f"{self.band}층{total}" if self.band else "층미상"


def parse_naver_floor(value: str) -> Floor:
    """'4/14', '저/14', 'B1/15' 형태를 해석한다."""
    text = re.sub(r"\s+", "", _text(value))
    head, _, tail = text.partition("/")
    total = int(tail) if tail.isdigit() else None
    head = head.replace("층", "")
    if head.isdigit():
        exact = int(head)
        return Floor(exact, band_of(exact, total), total)
    if head and head[0] in "저중고":
        return Floor(None, head[0], total)
    return Floor(None, "", total)


def parse_source_floor(unit: str, band: str) -> Floor:
    """매물장 '층/호'(405, 4층, 저층)와 '층수'(저/중/고)를 해석한다."""
    text = re.sub(r"\s+", "", _text(unit))
    band_text = _text(band)[:1]
    band_text = band_text if band_text in ("저", "중", "고") else ""
    match = re.fullmatch(r"(\d{3,4})(호)?", text)
    if match:
        exact = int(match.group(1)) // 100
        return Floor(exact, band_text or band_of(exact, None), None)
    match = re.fullmatch(r"(\d{1,2})층", text)
    if match:
        exact = int(match.group(1))
        return Floor(exact, band_text or band_of(exact, None), None)
    if text and text[0] in "저중고":
        return Floor(None, text[0], None)
    return Floor(None, band_text, None)


def floors_compatible(a: Floor, b: Floor) -> bool:
    if a.exact is not None and b.exact is not None:
        return a.exact == b.exact
    if a.exact is not None and b.band:
        return _exact_in_band(a.exact, b.band, b.total or a.total)
    if b.exact is not None and a.band:
        return _exact_in_band(b.exact, a.band, a.total or b.total)
    if a.band and b.band:
        return a.band == b.band
    return True  # 층 정보가 없으면 층으로는 구분하지 않는다


def _exact_in_band(exact: int, band: str, total: int | None) -> bool:
    """구간 경계는 1개 층 여유를 둔다(업소마다 저/중/고 기준이 조금씩 다름)."""
    candidates = {band_of(exact, total), band_of(max(1, exact - 1), total), band_of(exact + 1, total)}
    return band in candidates


def parse_source_price_man(value: str) -> int | None:
    """매물장 가격('46', '65/66', '57~8', '50억', '45억 5000')을 만원 단위로."""
    text = _text(value).replace(",", "")
    if not text:
        return None
    if "억" in text:
        price, _ = parse_price(text.split("/")[0].split("~")[0])
        return price
    match = re.match(r"(\d+(?:\.\d+)?)", text)
    return int(round(float(match.group(1)) * 10000)) if match else None


def _price_close(a: int | None, b: int | None, absolute: int, ratio: float) -> bool:
    if a is None or b is None:
        return True
    return abs(a - b) <= max(absolute, int(max(a, b) * ratio))


def _eok(value: int | float | None) -> str:
    if value is None:
        return ""
    eok = value / 10000
    return f"{eok:.1f}".rstrip("0").rstrip(".")


@dataclass
class Ad:
    complex_name: str
    dong: str
    pyeong: int | None
    floor: Floor
    price: int | None
    broker_key: str
    broker_name: str
    article_no: str
    registered: str
    owner: bool
    area: str
    raw: dict[str, str] = field(repr=False, default_factory=dict)


def broker_name_key(value: str) -> str:
    return re.sub(r"[\s()（）]+", "", _text(value)).lower()


def build_broker_alias(rows: Iterable[dict[str, str]]) -> dict[str, dict[str, str]]:
    """'압구정 중개업소 2' 탭(ID·중개업소명·실제상호)에서 ID/이름 → 실제상호 매핑을 만든다."""
    by_id: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for row in rows:
        canon = _text(row.get("실제상호"))
        if not canon:
            continue
        broker_id = _text(row.get("ID") or row.get("중개업소ID")).lstrip("'")
        name = _text(row.get("중개업소명"))
        if broker_id:
            by_id.setdefault(broker_id, canon)
        if name:
            by_name.setdefault(broker_name_key(name), canon)
    return {"byId": by_id, "byName": by_name}


def ads_from_rows(rows: Iterable[dict[str, str]],
                  broker_alias: dict[str, dict[str, str]] | None = None) -> list[Ad]:
    """중복처리 전 개별 광고 행(매매만)을 Ad 목록으로 변환한다.

    broker_alias가 있으면 업소명을 실제상호로 바꾸고, 같은 실제상호는 한 업소로 센다.
    """
    alias = broker_alias or {"byId": {}, "byName": {}}
    ads: list[Ad] = []
    seen: set[str] = set()
    for row in rows:
        complex_name = _text(row.get("단지명"))
        if _text(row.get("거래구분")) != "매매" or complex_name in EXCLUDED_COMPLEXES:
            continue
        article_no = _text(row.get("매물번호")).lstrip("'")
        if article_no and article_no in seen:
            continue
        seen.add(article_no)
        price, monthly = parse_price(row.get("가격", ""))
        if monthly is not None:
            continue
        broker_id = _text(row.get("중개업소ID")).lstrip("'")
        broker_name = _text(row.get("중개업소"))
        canon = alias["byId"].get(broker_id) if broker_id else None
        canon = canon or alias["byName"].get(broker_name_key(broker_name))
        if canon:
            broker_key = "canon:" + broker_name_key(canon)
            broker_name = canon
        else:
            broker_key = f"id:{broker_id}" if broker_id else "name:" + broker_name_key(broker_name)
        if _text(row.get("집주인")) == "집주인":
            broker_key = f"owner:{article_no}"
        ads.append(Ad(
            complex_name=complex_name,
            dong=_dong_no(row.get("동", "")),
            pyeong=_pyeong_no(nominal_pyeong(complex_name, row.get("면적", ""), row.get("동", ""))),
            floor=parse_naver_floor(row.get("층수", "")),
            price=price,
            broker_key=broker_key,
            broker_name=broker_name,
            article_no=article_no,
            registered=_text(row.get("등록일자")),
            owner=_text(row.get("집주인")) == "집주인",
            area=_text(row.get("면적")),
            raw=dict(row),
        ))
    return ads


@dataclass
class Cluster:
    ads: list[Ad]

    @property
    def exact_floors(self) -> set[int]:
        return {a.floor.exact for a in self.ads if a.floor.exact is not None}

    @property
    def brokers(self) -> set[str]:
        return {a.broker_key for a in self.ads}

    def floor_breakdown(self) -> str:
        """'13층(1)+고층(7)'처럼 정확층·구간 표기 광고 수를 나눠 보여준다."""
        floor = self.floor()
        total = f"/{floor.total}" if floor.total else ""
        exact_n = sum(1 for a in self.ads if a.floor.exact is not None)
        bands = Counter(a.floor.band or "층미상" for a in self.ads if a.floor.exact is None)
        parts = []
        if exact_n:
            parts.append(f"{floor.exact}층({exact_n})")
        for band, n in bands.most_common():
            label = f"{band}층" if band in ("저", "중", "고") else band
            parts.append(f"{label}({n})")
        return "+".join(parts) + total

    @property
    def prices(self) -> list[int]:
        return [a.price for a in self.ads if a.price is not None]

    def floor(self) -> Floor:
        exact = self.exact_floors
        totals = [a.floor.total for a in self.ads if a.floor.total]
        total = Counter(totals).most_common(1)[0][0] if totals else None
        if exact:
            value = next(iter(exact))
            return Floor(value, band_of(value, total), total)
        bands = Counter(a.floor.band for a in self.ads if a.floor.band)
        return Floor(None, bands.most_common(1)[0][0] if bands else "", total)

    def representative_price(self) -> int | None:
        prices = self.prices
        if not prices:
            return None
        counts = Counter(prices)
        top = max(counts.values())
        if top > 1:
            return min(p for p, c in counts.items() if c == top)
        return int(median(prices))


def _can_merge(a: Cluster, b: Cluster) -> bool:
    if len(a.exact_floors | b.exact_floors) > 1:
        return False
    for x in a.ads:
        for y in b.ads:
            if not floors_compatible(x.floor, y.floor):
                return False
            if not _price_close(x.price, y.price, CLUSTER_PRICE_TOLERANCE_MAN, CLUSTER_PRICE_TOLERANCE_RATIO):
                return False
    return True


def _greedy_cluster(members: list[Ad]) -> list[Cluster]:
    """가격순으로 하나씩 붙이되, 붙을 수 있는 묶음 중 가격이 가장 가까운 곳에 붙인다."""
    members = sorted(members, key=lambda a: a.price or 0)
    local: list[Cluster] = []
    for ad in members:
        single = Cluster([ad])
        best = None
        for cand in local:
            if _pyeong_close(cand, ad) and _can_merge(cand, single):
                gap = abs((cand.representative_price() or 0) - (ad.price or 0))
                if best is None or gap < best[0]:
                    best = (gap, cand)
        if best:
            best[1].ads.append(ad)
        else:
            local.append(single)
    return local


def cluster_ads(ads: Iterable[Ad]) -> list[Cluster]:
    """같은 실물로 보이는 광고를 묶는다(보수적 병합).

    1) 정확층 광고끼리 묶는다(같은 층·가격 근접).
    2) 저/중/고 구간만 적힌 광고끼리 묶는다(같은 구간·가격 근접).
    3) 구간 묶음과 정확층 묶음은 합치지 않는다. 고층 45억이 13층인지 다른 호수인지
       광고만으로는 알 수 없기 때문이다.
    """
    groups: dict[tuple[str, str], list[Ad]] = {}
    for ad in ads:
        groups.setdefault((ad.complex_name, ad.dong), []).append(ad)

    clusters: list[Cluster] = []
    for _, members in sorted(groups.items()):
        exact_ads = [a for a in members if a.floor.exact is not None]
        band_ads = [a for a in members if a.floor.exact is None]
        exact_clusters: list[Cluster] = []
        by_floor: dict[int, list[Ad]] = {}
        for a in exact_ads:
            by_floor.setdefault(a.floor.exact, []).append(a)
        for floor_ads in by_floor.values():
            exact_clusters.extend(_greedy_cluster(floor_ads))
        band_clusters = _greedy_cluster(band_ads)

        # 구간 표기 광고는 정확층 광고와 합치지 않는다. 같은 동·구간에 광고에 안 나온
        # 다른 호수(예: 저층 46억 = 405호)가 있을 수 있어, 합치면 매물장 대조가 틀어진다.
        leftover = band_clusters
        clusters.extend(exact_clusters)
        clusters.extend(leftover)
    return clusters


def _pyeong_close(cluster: Cluster, ad: Ad) -> bool:
    values = [a.pyeong for a in cluster.ads if a.pyeong is not None]
    if not values or ad.pyeong is None:
        return True
    return abs(values[0] - ad.pyeong) <= PYEONG_TOLERANCE


@dataclass
class SourceItem:
    complex_name: str
    dong: str
    pyeong: int | None
    floor: Floor
    price: int | None
    status: str
    unit_text: str
    price_text: str
    updated: str


def source_items_from_rows(rows: Iterable[dict[str, str]]) -> list[SourceItem]:
    items: list[SourceItem] = []
    for row in rows:
        complex_name = _text(row.get("단지명"))
        complex_name = SOURCE_COMPLEX_ALIASES.get(complex_name, complex_name)
        pyeong = _pyeong_no(row.get("평형", ""))
        dong = _dong_no(row.get("동", ""))
        if not complex_name or pyeong is None or not dong:
            continue
        items.append(SourceItem(
            complex_name=complex_name,
            dong=dong,
            pyeong=pyeong,
            floor=parse_source_floor(row.get("층/호", ""), row.get("층수", "")),
            price=parse_source_price_man(row.get("가격", "")),
            status=_text(row.get("상태")) or "상태미기재",
            unit_text=_text(row.get("층/호")),
            price_text=_text(row.get("가격")),
            updated=_text(row.get("수정일")) or _text(row.get("등록일")),
        ))
    return items


def _is_active(status: str) -> bool:
    return status in ("활성", "상태미기재")


def judge_cluster(cluster: Cluster, sources: list[SourceItem]) -> tuple[str, list[SourceItem]]:
    """묶음 하나를 매물장과 대조해 (판정, 관련 매물장 항목)을 돌려준다."""
    ad = cluster.ads[0]
    floor = cluster.floor()
    price = cluster.representative_price()
    pyeongs = [a.pyeong for a in cluster.ads if a.pyeong is not None]
    pyeong = pyeongs[0] if pyeongs else None

    candidates = [
        s for s in sources
        if s.complex_name == ad.complex_name and s.dong == ad.dong
        and (pyeong is None or s.pyeong is None or abs(s.pyeong - pyeong) <= PYEONG_TOLERANCE)
        and floors_compatible(floor, s.floor)
    ]
    if not candidates:
        return STATUS_UNREGISTERED, []

    active = [s for s in candidates if _is_active(s.status)]
    exact_hits = [
        s for s in active
        if floor.exact is not None and s.floor.exact is not None and s.floor.exact == floor.exact
    ]
    if exact_hits:
        return STATUS_REGISTERED, exact_hits
    near = [
        s for s in active
        if _price_close(s.price, price, REGISTER_PRICE_TOLERANCE_MAN, REGISTER_PRICE_TOLERANCE_RATIO)
    ]
    if near:
        return STATUS_REGISTERED, near
    if active:
        return STATUS_PRICE_DIFF, active
    inactive_near = [
        s for s in candidates
        if (floor.exact is not None and s.floor.exact == floor.exact)
        or _price_close(s.price, price, REGISTER_PRICE_TOLERANCE_MAN, REGISTER_PRICE_TOLERANCE_RATIO)
    ]
    if inactive_near:
        return STATUS_INACTIVE, inactive_near
    # 같은 동·층대의 거래완료·보류 건은 가격이 크게 달라 다른 집으로 본다
    return STATUS_UNREGISTERED, candidates


RESULT_HEADERS = [
    "판정", "구역", "단지명", "평형", "동", "층", "대표가(억)", "가격범위(억)",
    "업소수", "광고수", "집주인", "최초등록일", "최근등록일", "중개업소",
    "매물장 후보(층/호·가격·상태)", "면적", "매물번호",
]


def _align_pyeong_to_source(ads: list[Ad], sources: list[SourceItem]) -> None:
    """면적 환산 평형(예: 36평)을 매물장의 통상 평형(예: 35평)으로 맞춘다."""
    known: dict[tuple[str, str], Counter] = {}
    for s in sources:
        if s.pyeong is not None:
            known.setdefault((s.complex_name, s.dong), Counter())[s.pyeong] += 1
    for a in ads:
        options = known.get((a.complex_name, a.dong))
        if not options or a.pyeong is None:
            continue
        # 매물장에서 이 동의 평형이 하나뿐이면 면적 환산 오차가 커도 그 평형으로 본다
        # (예: 현대14차 205동 면적 환산 29평 → 32평).
        if len(options) == 1:
            only = next(iter(options))
            if abs(only - a.pyeong) <= ALIGN_SINGLE_TYPE_TOLERANCE:
                a.pyeong = only
            continue
        near = [p for p in options if abs(p - a.pyeong) <= PYEONG_TOLERANCE]
        if near:
            a.pyeong = min(near, key=lambda p: (abs(p - a.pyeong), -options[p]))


def build_results(rows: Iterable[dict[str, str]], source_rows: Iterable[dict[str, str]],
                  min_brokers: int = MIN_BROKERS_FOR_GENUINE,
                  broker_alias: dict[str, dict[str, str]] | None = None) -> list[list[Any]]:
    ads = ads_from_rows(rows, broker_alias)
    sources = source_items_from_rows(source_rows)
    _align_pyeong_to_source(ads, sources)
    output: list[tuple[tuple, list[Any]]] = []
    for cluster in cluster_ads(ads):
        if len(cluster.brokers) < min_brokers:
            continue
        status, related = judge_cluster(cluster, sources)
        floor = cluster.floor()
        prices = cluster.prices
        dates = sorted(a.registered for a in cluster.ads if a.registered and a.registered != "Unknown")
        names = sorted({a.broker_name for a in cluster.ads if not a.owner and a.broker_name})
        if any(a.owner for a in cluster.ads):
            names.insert(0, "(집주인)")
        pyeongs = [a.pyeong for a in cluster.ads if a.pyeong is not None]
        ad0 = cluster.ads[0]
        related_text = "; ".join(
            f"{s.unit_text or s.floor.label()}·{s.price_text}·{s.status}" for s in related[:4]
        )
        rep = cluster.representative_price()
        row = [
            status,
            ZONE_BY_COMPLEX.get(ad0.complex_name, ""),
            ad0.complex_name,
            f"{pyeongs[0]}평" if pyeongs else "",
            f"{ad0.dong}동",
            cluster.floor_breakdown(),
            _eok(rep),
            f"{_eok(min(prices))}~{_eok(max(prices))}" if prices else "",
            len(cluster.brokers),
            len(cluster.ads),
            "Y" if any(a.owner for a in cluster.ads) else "",
            dates[0] if dates else "",
            dates[-1] if dates else "",
            ", ".join(names),
            related_text,
            Counter(a.area for a in cluster.ads).most_common(1)[0][0],
            " ".join(a.article_no for a in cluster.ads),
        ]
        # 판정 순, 같은 판정 안에서는 최근에 처음 올라온 매물부터
        first_seen = int(re.sub(r"\D", "", row[11]) or 0)
        sort_key = (STATUS_ORDER[status], -first_seen, row[1], row[2], row[4])
        output.append((sort_key, row))
    output.sort(key=lambda item: item[0])
    return [row for _, row in output]
