# 수학 도표 렌더러 / HWP 문항 생성기 / 기출이미지생성기

학원 수학 문항 제작용 Windows 프로그램 모음입니다.

## 포함 파일

- `수학도표렌더러.exe`: TXT의 `[IMAGE_PROMPT:]` 블록을 읽어 PNG 이미지를 생성합니다.
- `HWP문항생성기.exe`: 문제 TXT와 생성된 PNG를 읽어 HWP 문제지를 생성합니다.
- `기출이미지생성기.exe`: 기출 문제 이미지 템플릿을 등록하고, 변수값만 바꿔 유사 이미지를 생성합니다.
- `src/`: 도표 렌더러와 HWP 생성기 Python 소스 및 빌드 설정입니다.
- `past_exam_image_builder/`: 기출이미지생성기 Python 소스 및 빌드 설정입니다.
- `apps-script/integrated_master_Code.gs`: 중앙 마스터/선생님 파일 연동용 Apps Script 통합본입니다.

## 기본 사용 순서

1. 문제 TXT 파일을 `수학도표렌더러.exe`로 드래그합니다.
2. TXT 파일과 같은 폴더에 `파일명_이미지1.PNG` 형식의 이미지가 생성됩니다.
3. 같은 TXT 파일을 `HWP문항생성기.exe`로 드래그합니다.
4. TXT 파일과 같은 폴더에 HWP 파일이 생성됩니다.

명령 프롬프트에서도 실행할 수 있습니다.

```powershell
.\수학도표렌더러.exe "C:\문제폴더\문제.txt"
.\HWP문항생성기.exe "C:\문제폴더\문제.txt"
```

## 기출이미지생성기 사용

`기출이미지생성기.exe`를 실행하면 실행파일과 같은 폴더에 `library` 폴더가 자동 생성됩니다.

- 등록 탭에서 PDF/이미지를 불러와 문항별 이미지 템플릿을 저장합니다.
- 생성 탭에서 저장된 템플릿을 고르고 변수값을 넣어 새 이미지를 생성합니다.
- 실제 기출 이미지 라이브러리는 저작권 및 내부 자료 성격이 있어 GitHub에는 포함하지 않습니다.

## 실행 환경

- Windows 10/11
- HWP 생성기는 한글 프로그램이 설치된 PC가 필요합니다.
- Windows에서 다운로드한 EXE가 차단되면 파일 속성에서 `차단 해제`를 선택합니다.

## Apps Script 트리거

통합 Apps Script에는 무료 작업큐와 유료 문항생성큐 트리거가 분리되어 있습니다.

- `무료 작업큐 트리거 설치`: `processQueue` 1개만 설치합니다.
- `유료 문항생성큐 트리거 설치`: `processGenerationQueue` 병렬 작업용 트리거를 설치합니다.
- `무료+유료 트리거 전체 설치`: 두 종류를 모두 설치합니다.

## 빌드

Python과 PyInstaller가 설치된 환경에서 다음 명령으로 빌드합니다.

```powershell
pyinstaller --noconfirm --clean src/수학도표렌더러.spec
pyinstaller --noconfirm --clean src/HWPProblemBuilder.spec
pyinstaller --noconfirm --clean past_exam_image_builder/기출이미지생성기.spec
```
