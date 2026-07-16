"""Business-type, region, and license reference data for the classifier.

Pure lookup tables and compiled patterns used by the text-normalization helpers
and the categorical (business-type / license / region) axes. Declared once here
so the scoring modules share a single source of truth.
"""

import re

BUSINESS_TYPE_ALIASES = {
    "software": {"software", "소프트웨어", "sw", "ict", "정보화"},
    "technical-service": {"technical-service", "기술용역", "엔지니어링", "설계", "감리"},
    "service": {"service", "general-service", "일반용역", "용역"},
    "goods": {"goods", "물품", "구매", "납품"},
    "construction": {"construction", "공사", "건설"},
    "other": {"other", "기타"},
}
RELATED_BUSINESS_TYPES = {
    "software": {"technical-service", "service"},
    "technical-service": {"software", "service"},
    "service": {"software", "technical-service"},
    "goods": set(),
    "construction": set(),
    "other": set(),
}
REGION_ALIASES = {
    "전국": ("전국",),
    "서울": ("서울", "서울시", "서울특별시"),
    "부산": ("부산", "부산시", "부산광역시"),
    "대구": ("대구", "대구시", "대구광역시"),
    "인천": ("인천", "인천시", "인천광역시"),
    "광주": ("광주", "광주시", "광주광역시"),
    "대전": ("대전", "대전시", "대전광역시"),
    "울산": ("울산", "울산시", "울산광역시"),
    "세종": ("세종", "세종시", "세종특별자치시"),
    "경기": ("경기", "경기도"),
    "강원": ("강원", "강원도", "강원특별자치도"),
    "충북": ("충북", "충청북도"),
    "충남": ("충남", "충청남도"),
    "전북": ("전북", "전라북도", "전북특별자치도"),
    "전남": ("전남", "전라남도"),
    "경북": ("경북", "경상북도"),
    "경남": ("경남", "경상남도"),
    "제주": ("제주", "제주도", "제주특별자치도"),
}
LICENSE_CODE_PATTERN = re.compile(r"\b[A-Z]{2,}\d{2,}\b")
LICENSE_CONTEXT_KEYWORDS = ("면허", "자격", "등록", "보유", "필수", "필요", "요건", "제한")
LICENSE_ALIASES = {
    "SW001": ("SW001", "소프트웨어사업자", "소프트웨어 사업자", "sw사업자"),
    "NET001": ("NET001", "정보통신공사업", "정보통신공사"),
    "ENG001": ("ENG001", "엔지니어링사업", "엔지니어링", "기술사", "감리"),
    "SEC001": ("SEC001", "정보보호전문서비스", "보안관제", "isms"),
    "ELE001": ("ELE001", "전기공사업", "전기"),
    "FIRE001": ("FIRE001", "소방시설", "소방"),
    # 건설 면허 (front stores the exact "○○공사업" strings; aliases below must
    # contain them verbatim so profile.license_codes ↔ project requirement
    # matching works). Bare "건축"/"토목"/"기계"/"가스"/"조경" are deliberately
    # NOT aliases — they are too generic and would over-match.
    "ARC001": ("ARC001", "건축공사업"),
    "CIV001": ("CIV001", "토목공사업"),
    "CIVARC001": ("CIVARC001", "토목건축공사업"),
    "LND001": ("LND001", "조경공사업"),
    "ENV001": ("ENV001", "산업환경설비공사업", "산업·환경설비공사업", "산업·환경설비"),
    "INT001": ("INT001", "실내건축공사업"),
    "MEC001": ("MEC001", "기계설비공사업"),
    "GAS001": ("GAS001", "가스시설공사업"),
    # 해양 엔지니어링·기술용역 면허 (해양엔지니어링협회 게이트, Phase 1).
    # 시공(공사) 면허가 아니라 설계·감리·조사·측량 등 기술용역 면허군이다.
    # bare 단어("해양"/"항만"/"측량")는 과매칭이라 별칭에서 제외하고
    # 다자 합성어만 별칭으로 둔다(기존 건설 면허 주석과 동일 원칙).
    # "…기술사" 접미 별칭은 두지 않는다: "기술사"는 기존 ENG001 별칭이라
    # 어차피 ENG001과 함께 잡히고(엔지니어링 계열), 루트 별칭이 substring으로
    # 커버하므로 중복일 뿐이다(추출 결과 불변, 중복만 제거).
    "PORT001": ("PORT001", "항만및해안", "항만설계"),
    "MAR001": ("MAR001", "해양엔지니어링"),
    "HYDRO001": ("HYDRO001", "수로조사", "수로측량", "해양조사"),
}
REGION_RESTRICTION_KEYWORDS = (
    "지역제한",
    "소재 업체",
    "소재업체",
    "소재지",
    "업체만",
    "입찰 가능",
    "참여 가능",
    "관할",
    "등록 업체",
    "제한경쟁",
)
COMPLEXITY_KEYWORDS = {
    "통합",
    "고도화",
    "운영",
    "유지관리",
    "24시간",
    "대규모",
    "다기관",
    "클라우드",
    "센터",
}
