"""KONEPS OpenAPI 소스 별칭 · 카테고리→오퍼레이션 라우팅 **선언 테이블**.

``openapi.py`` 가 한도(500줄) 경계까지 자라 여유가 0~4줄이었다. 그래서 그 모듈에서
**해석 코드(함수)** 와 **선언 데이터(표)** 를 책임 단위로 갈랐다: 여기는 값 집합만 두고
(§4.5-1 선언적 구성), 표를 읽어 오퍼레이션을 고르는 순수 셀렉터 함수는 ``openapi.py`` 에
남는다(그 함수들의 테스트도 그대로 그 자리에 남는다).

이 모듈에는 함수가 없다 — 새 카테고리·별칭 추가는 코드가 아니라 **데이터 한 줄** 추가로
끝난다. 각 오퍼레이션 표는 같은 카테고리 키 집합(내부 카테고리 + 한글 별칭)을 유지해야
하며, 미지 키의 기본 오퍼레이션은 표가 아니라 셀렉터가 선언한다(``.get(key, default)``).
"""

OPENAPI_SOURCE_ALIASES = {
    "koneps-openapi",
    "koneps_api",
    "koneps-api",
    "koneps-public-api",
    "bid-public-info",
}
SCSBID_OPENAPI_SOURCE_ALIASES = {
    "koneps-scsbid",
    "koneps-award-openapi",
    "koneps-awards",
    "scsbid",
    "scsbid-openapi",
}
OPENAPI_CATEGORY_OPERATIONS = {
    "construction": "getBidPblancListInfoCnstwk",
    "공사": "getBidPblancListInfoCnstwk",
    "service": "getBidPblancListInfoServc",
    "general-service": "getBidPblancListInfoServc",
    "technical-service": "getBidPblancListInfoServc",
    "software": "getBidPblancListInfoServc",
    "용역": "getBidPblancListInfoServc",
    "goods": "getBidPblancListInfoThng",
    "물품": "getBidPblancListInfoThng",
    "foreign": "getBidPblancListInfoFrgcpt",
    "frgcpt": "getBidPblancListInfoFrgcpt",
    "외자": "getBidPblancListInfoFrgcpt",
}
SCSBID_CATEGORY_OPERATIONS = {
    "construction": "getScsbidListSttusCnstwk",
    "공사": "getScsbidListSttusCnstwk",
    "service": "getScsbidListSttusServc",
    "general-service": "getScsbidListSttusServc",
    "technical-service": "getScsbidListSttusServc",
    "software": "getScsbidListSttusServc",
    "용역": "getScsbidListSttusServc",
    "goods": "getScsbidListSttusThng",
    "물품": "getScsbidListSttusThng",
    "foreign": "getScsbidListSttusFrgcpt",
    "frgcpt": "getScsbidListSttusFrgcpt",
    "외자": "getScsbidListSttusFrgcpt",
}
SCSBID_RESERVE_DETAIL_OPERATIONS = {
    "construction": "getOpengResultListInfoCnstwkPreparPcDetail",
    "공사": "getOpengResultListInfoCnstwkPreparPcDetail",
    "service": "getOpengResultListInfoServcPreparPcDetail",
    "general-service": "getOpengResultListInfoServcPreparPcDetail",
    "technical-service": "getOpengResultListInfoServcPreparPcDetail",
    "software": "getOpengResultListInfoServcPreparPcDetail",
    "용역": "getOpengResultListInfoServcPreparPcDetail",
    "goods": "getOpengResultListInfoThngPreparPcDetail",
    "물품": "getOpengResultListInfoThngPreparPcDetail",
    "foreign": "getOpengResultListInfoFrgcptPreparPcDetail",
    "frgcpt": "getOpengResultListInfoFrgcptPreparPcDetail",
    "외자": "getOpengResultListInfoFrgcptPreparPcDetail",
}
# 개찰 1위(잠정) 결과 목록 오퍼레이션. ScsbidInfoService ``getOpengResultListInfo*``
# 계열로, 행에 ``opengCorpInfo``(1위 캐럿 문자열) + ``prtcptCnum``(참가자수)을 싣는다
# (실측 2026-07-19). reserve-detail 계열과 같은 카테고리 키 집합을 유지한다.
SCSBID_OPENING_RESULT_OPERATIONS = {
    "construction": "getOpengResultListInfoCnstwk",
    "공사": "getOpengResultListInfoCnstwk",
    "service": "getOpengResultListInfoServc",
    "general-service": "getOpengResultListInfoServc",
    "technical-service": "getOpengResultListInfoServc",
    "software": "getOpengResultListInfoServc",
    "용역": "getOpengResultListInfoServc",
    "goods": "getOpengResultListInfoThng",
    "물품": "getOpengResultListInfoThng",
    "foreign": "getOpengResultListInfoFrgcpt",
    "frgcpt": "getOpengResultListInfoFrgcpt",
    "외자": "getOpengResultListInfoFrgcpt",
}
