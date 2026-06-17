🤖 [지시서] AI 기반 나라장터 스마트 입찰 분석 시스템 구축

1. 프로젝트 개요 (Project Overview)
• 목표: 나라장터(KONEPS) 공고를 실시간 크롤링하여, 업체별 맞춤형 공고를 분류하고, AI 모델(LSTM/Ensemble)을 통해 최적의 투찰 금액을 제안하는 자동화 시스템 구축.
• 핵심 가치: 낙찰 확률 극대화, 데이터 기반 사정률 예측
• 개발 환경: macOS, Python 3.10+, VS Code (with AI Agent).
2. 시스템 아키텍처 및 모듈 구성
[모듈 1] 나라장터 실시간 크롤러 (Collector Module)
• 기능: 신규 공고 및 개찰 결과 데이터 수집.
• 기술 스택: Playwright 또는 Selenium (나라장터의 동적 페이지 대응), BeautifulSoup4.
• 상세 요구사항: • 공고명, 기초금액, 추정가격, 입찰마감일, 업종 제한, 지역 제한 정보 추출. • 개찰 결과 페이지에서 15개 복수예비가격 및 선택된 4개 번호 데이터 수집. • Anti-Bot 회피: fake-useragent 및 적절한 sleep 타임 설정.
[모듈 2] 맞춤형 공고 분류기 (Classifier Module)
• 기능: 업체 프로필(면허, 지역, 매출, 실적)과 공고문(RFP) 매칭.
• 기술 스택: Sentence-Transformers (Semantic Similarity), Scikit-learn.
• 상세 요구사항: • 벡터 공간 확장: 키워드 매칭을 넘어 LLM 기반 임베딩을 통해 의미론적 유사도 분석 (유사도 85% 이상 공고 분류). • 필터링 로직: 업무 구분(물품/용역/공사), 면허 코드 일치 여부, 추정가격 대비 업체 시공능력평가액 대조.
[모듈 3] 데이터 엔진 및 AI 분석 (Brain Module)
• 기능: 투찰 금액 산출용 사정률 예측 (리포트 내 공학적 분석 반영).
• 기술 스택: Pandas, NumPy, PyTorch/TensorFlow (LSTM), SciPy (T-distribution).
• 상세 요구사항: • LSTM 기반 에이전시 메모리: 특정 발주처의 과거 사정률 흐름을 시계열 데이터로 학습. • T-분포 보정: 데이터 샘플이 적은 경우(N=10 내외)에도 통계적 유의성 확보. • 공정 분배 로직: 시스템 내부 DB의 '낙찰 횟수'를 조회하여, 낙찰이 적은 업체에 최상위 확률 구간 금액을 우선 배정하는 함수 구현. • 계산 공식: 투찰금액 = 기초금액 *예측 사정률* 낙찰하한율 (업종별 하한율 자동 적용).
[모듈 4] 텔레그램 알림 및 제어 (Interface Module)
• 기능: 분석 결과 전송 및 사용자 인터랙션.
• 기술 스택: python-telegram-bot.
• 상세 요구사항: • 실시간 푸시: 적격 공고 발생 시 [공고번호/사업명/기초금액/AI 추천가/낙찰확률] 전송. • 인라인 버튼: [상세보기(URL)], [투찰완료 기록], [관심제외] 버튼 구성.
3. 데이터베이스 설계 (Data Schema)
• SQLite 또는 PostgreSQL 사용 • Bids: 공고 기본 정보 및 분석 결과. • Historical_Data: 과거 20년치 개찰 데이터 (사정률, 예정가격). • Users: 업체 정보 (면허, 실적, 과거 낙찰 이력). • Allocations: 사용자별 배정된 투찰가 기록 (중복 방지용).
4. AI Agent를 위한 단계별 코딩 지시 (Prompting Sequence)
Step 1: 프로젝트 구조 생성
"Python 기반의 나라장터 분석 프로젝트 구조를 Mac 환경에 맞춰 생성해줘. requirements.txt에는 playwright, pandas, python-telegram-bot, scikit-learn, torch를 포함하고, 각 모듈별로 디렉토리를 나눠줘."
Step 2: 크롤러 구현
"Playwright를 사용하여 나라장터 입찰공고 검색 페이지에서 오늘 올라온 '소프트웨어' 관련 공고 리스트를 긁어오는 스크립트를 작성해줘. headless 모드로 작동해야 하며, 공고번호와 기초금액은 필수로 포함해야 해."
Step 3: AI 사정률 예측 로직 구현
"제공된 리포트의 사정률 예측 공식을 파이썬 함수로 만들어줘. 과거 사정률 리스트(N=20)를 입력받아 LSTM 모델과 T-분포 보정을 적용해 가장 확률이 높은 사정률 3개를 반환해야 해. 기초금액의 ±2~3% 범위를 고려해줘."
Step 4: 텔레그램 메시징 및 배분 로직
"분석된 결과를 텔레그램으로 보낼 때, 내부 DB를 조회해서 낙찰 이력이 적은 사용자에게 가장 높은 확률의 금액을 먼저 배정하는 로직을 추가해줘. 사용자가 버튼을 누르면 해당 공고가 '투찰 중' 상태로 업데이트되어야 해."
5. 보안 및 리스크 관리 (Developer Notes)
• Environment Variables: 모든 API Key(Telegram, DB)는 .env 파일로 관리할 것.
• Human-in-the-loop: AI가 계산한 투찰가가 법적 낙찰하한선 미만으로 내려가지 않도록 하는 Verification_Module을 반드시 거칠 것.
• Error Handling: 크롤링 실패 시 재시도 로직 및 관리자 알림 기능 포함.
이 지시서를 Cursor나 ChatGPT Plus(Advanced Data Analysis)에 입력하시면, 각 모듈별로 구체적인 코드를 생성하고 통합하는 과정을 시작할 수 있습니다. 개발 중 특정 모듈의 알고리즘(예: LSTM 가중치 설정)에 대해 더 자세한 코드가 필요하시면 말씀해주세요!
