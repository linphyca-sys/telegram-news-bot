# 텔레그램 키워드 뉴스봇

설정한 키워드로 뉴스를 주기적으로(기본 5분) 검색해서, **새로 올라온 기사만** 텔레그램으로 보내주는 봇입니다.

전송 예시:

> 🔍 **#동료지원인**
>
> **동료지원 제도화, 정신질환·장애 당사자 32명 동료지원인 양성교육 수료**
>
> 더불어민주당 김윤 의원은 "환자들한테 배운다"는 의료계 격언을 인용하며...
>
> 📅 2026.07.02. 오전 9:04
> 🔗 뉴스 전문 보기

## 설치

```powershell
cd C:\Users\linph\telegram-news-bot
pip install -r requirements.txt
```

## 설정 (3단계)

### 1. 텔레그램 봇 만들기
1. 텔레그램에서 **@BotFather** 검색 → 대화 시작
2. `/newbot` 입력 → 봇 이름/아이디 입력
3. 발급된 **토큰**(예: `123456:ABC-DEF...`)을 복사

### 2. .env 파일 만들기
```powershell
copy .env.example .env
```
`.env` 파일을 열어 `TELEGRAM_BOT_TOKEN`과 `KEYWORDS`를 채웁니다.

### 3. chat_id 확인
1. 텔레그램에서 방금 만든 봇을 검색해 **아무 메시지나 하나 보냅니다** (필수!)
2. 아래 실행:
   ```powershell
   python get_chat_id.py
   ```
3. 출력된 chat_id를 `.env`의 `TELEGRAM_CHAT_ID`에 넣습니다.

## 실행

```powershell
python news_bot.py
```

- 최초 실행 시 키워드별 최신 기사 3건을 보내고, 이후에는 새 기사만 전송합니다.
- 중지: `Ctrl+C`
- 이미 보낸 기사는 `seen_links.json`에 기록되어 중복 전송되지 않습니다.

## 키워드와 필터 (keywords.txt)

키워드는 `keywords.txt`에서 한 줄에 하나씩 관리합니다. 동명이인 등 원치 않는 기사를 거르는 필터를 붙일 수 있습니다:

```
김민석 | 제외: 야구, 홈런, 타율          ← 이 단어가 있으면 안 보냄
김민석 | 포함: 총리, 의원, 민주당        ← 이 단어 중 하나는 있어야 보냄
김민석 | 제외: 야구 | 포함: 총리, 의원   ← 둘 다 가능
```

필터는 기사 제목 + 요약문을 기준으로 적용됩니다. GitHub에 올려둔 경우 저장소 웹페이지에서 `keywords.txt`를 직접 수정하면 됩니다 (휴대폰 가능).

## 뉴스 소스

| 소스 | 조건 | 특징 |
|---|---|---|
| 구글 뉴스 RSS | 기본 (키 불필요) | 바로 사용 가능 |
| 네이버 뉴스 API | `.env`에 네이버 키 입력 시 자동 전환 | 한국 뉴스 커버리지·요약문 품질 좋음 (권장) |

네이버 키 발급: [developers.naver.com/apps](https://developers.naver.com/apps) → 애플리케이션 등록 → 사용 API에서 **검색** 선택 → Client ID / Secret을 `.env`에 입력.

## 백그라운드 실행 (선택)

창을 닫아도 계속 돌게 하려면:

```powershell
pythonw news_bot.py
```

또는 Windows 작업 스케줄러에 "로그인 시 실행"으로 등록하세요.
