# 수학 도표 렌더러 / HWP 문항 생성기

학원 수학 문항 제작용 Windows 프로그램입니다.

## 포함 파일

- `수학도표렌더러.exe`: TXT의 `[IMAGE_PROMPT:]` 블록을 읽어 PNG 이미지를 생성합니다.
- `HWP문항생성기.exe`: 문제 TXT와 생성된 PNG를 읽어 HWP 시험지를 생성합니다.
- `src/`: Python 원본 소스와 PyInstaller 빌드 설정입니다.

## 사용 순서

1. 문제 TXT 파일을 `수학도표렌더러.exe` 위로 끌어다 놓습니다.
2. TXT 파일과 같은 폴더에 `파일명_이미지1.PNG` 형식으로 이미지가 생성됩니다.
3. 같은 TXT 파일을 `HWP문항생성기.exe` 위로 끌어다 놓습니다.
4. TXT 파일과 같은 폴더에 HWP 파일이 생성됩니다.

명령 프롬프트에서도 실행할 수 있습니다.

```powershell
.\수학도표렌더러.exe "C:\문제폴더\문제.txt"
.\HWP문항생성기.exe "C:\문제폴더\문제.txt"
```

## 실행 환경

- Windows 10/11
- HWP 문항 생성기는 한글 프로그램이 설치된 PC가 필요합니다.
- Windows에서 다운로드한 EXE가 차단되면 파일 속성에서 `차단 해제`를 선택합니다.

## 주의

- 문제 TXT와 생성 이미지의 기본 파일명은 같아야 합니다.
- HWP 생성 전에 도표 렌더러를 먼저 실행합니다.
- 실행 중인 한글 문서가 있다면 저장한 후 HWP 생성기를 실행하는 것이 안전합니다.

## 소스 빌드

Python과 PyInstaller가 설치된 환경에서 다음 명령으로 빌드합니다.

```powershell
pyinstaller --noconfirm --clean src/수학도표렌더러.spec
pyinstaller --noconfirm --clean src/HWPProblemBuilder.spec
```
