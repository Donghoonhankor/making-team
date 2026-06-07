/**
 * Google Sheets Apps Script for math test analysis, student reports,
 * and similar-problem generation with queue-based Gemini API throttling.
 *
 * Install:
 * 1. Open Extensions > Apps Script in the target spreadsheet.
 * 2. Paste this entire file into Code.gs.
 * 3. Run setupSheets() once.
 * 4. Fill 관리자_설정 with API keys and Drive root folder ID.
 * 5. Run installQueueTrigger() once, or processQueue() manually for testing.
 */

const SHEETS = {
  PROBLEM_BANK: '문제은행',
  QUEUE: '작업큐',
  ADMIN: '관리자_설정',
  TWIN_RULES: '쌍둥이_규칙',
  API_LOG: 'API_사용로그',
  WRONG_HISTORY: '오답누적DB',
  WEAKNESS_SUMMARY: '학생약점요약DB',
  EXAM_LIST: '시험지목록',
  TYPE_MAPPING: '유형매핑'
};

const TASK_TYPES = {
  PROBLEM_ANALYSIS: 'PROBLEM_ANALYSIS',
  STUDENT_REPORT: 'STUDENT_REPORT',
  SIMILAR_PROBLEMS: 'SIMILAR_PROBLEMS'
};

const QUEUE_STATUS = {
  PENDING: 'PENDING',
  RUNNING: 'RUNNING',
  DONE: 'DONE',
  FAILED: 'FAILED'
};

const RESERVED_SHEETS = [
  SHEETS.PROBLEM_BANK,
  SHEETS.QUEUE,
  SHEETS.ADMIN,
  SHEETS.TWIN_RULES,
  SHEETS.API_LOG,
  SHEETS.WRONG_HISTORY,
  SHEETS.WEAKNESS_SUMMARY,
  SHEETS.EXAM_LIST,
  SHEETS.TYPE_MAPPING
];

const HEADERS = {
  PROBLEM_BANK: ['시험지 이름', '문제번호', '링크', '상위 단원', '하위 단원', '문제 유형', '표준 문제 유형', '정답', '풀이요약', '신뢰도', '검산메모', '처리상태', '오류메시지', '마지막처리시간'],
  TEACHER: ['학생 이름', '시험지 이름', '틀린 문제 번호', '분석 보고서', '쌍둥이 문항', '누적 분석 보고서', '처리상태', '오류메시지'],
  QUEUE: ['작업ID', '작업종류', '대상시트', '대상행', '상태', '재시도횟수', '예약시각', '오류메시지', '생성시간', '처리시간', '페이로드JSON'],
  ADMIN: ['기능', '적용시트', '프로젝트명', 'API키', 'RPM', 'TPM', 'RPD', '모델명', '1회처리개수', '요청간대기ms', '첨부토큰보정값', '출력토큰보정값', 'Drive루트폴더ID', '일반문항수', '이미지문항수', '사용여부'],
  TWIN_RULES: ['문제 유형', '기본문항수', '난이도', '생성규칙', '금지사항', '풀이포함여부', '사용여부'],
  TYPE_MAPPING: ['원본 문제 유형', '상위 단원', '하위 단원', '표준 문제 유형', '사용여부', '메모'],
  API_LOG: ['시간', '날짜', '기능', '프로젝트명', '모델명', 'API키끝4자리', '요청수', '예상입력토큰', '실제입력토큰', '출력토큰', '사고토큰', '캐시토큰', '총토큰', '상태', '오류메시지'],
  WRONG_HISTORY: ['중복키', '기록일시', '선생님시트', '학생 이름', '시험지 이름', '시험일', '문제번호', '문제 유형', '원본 문제 유형', '상위 단원', '하위 단원', '정답', '입력행', '보고서 링크', '쌍둥이 문항 링크'],
  WEAKNESS_SUMMARY: ['요약키', '학생 이름', '월', '상위 단원', '하위 단원', '문제 유형', '오답 횟수', '첫 오답일', '최근 오답일', '시험 횟수', '최근 시험지 이름', '선생님시트'],
  EXAM_LIST: ['시험지 이름']
};

const DEFAULT_MODEL = 'gemini-2.5-flash';
const DEFAULT_BATCH_SIZE = 6;
const DEFAULT_REQUEST_DELAY_MS = 12000;
const DEFAULT_STUDENT_COOLDOWN_MS = 60000;
const STALE_RUNNING_QUEUE_MS = 8 * 60 * 1000;
const MAX_RETRIES = 3;
const PERFECT_SCORE_TEXT = '오답 없음 (100점)';

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('AI 시험 자동화')
    .addItem('초기 시트 생성/정비', 'setupSheets')
    .addSeparator()
    .addItem('시험지목록 갱신', 'refreshExamList')
    .addItem('쌍둥이 규칙 초안 갱신', 'refreshTwinRuleDrafts')
    .addItem('유형매핑 초안 갱신', 'refreshTypeMappingDrafts')
    .addItem('유형매핑 적용', 'applyTypeMappings')
    .addItem('현재 시트를 선생님 시트로 초기화', 'setupCurrentTeacherSheet')
    .addSeparator()
    .addItem('문제은행 분석 작업 등록', 'enqueueProblemAnalysisTasks')
    .addItem('현재 시트 보고서 작업 등록', 'enqueueStudentReportTasks')
    .addItem('현재 시트 쌍둥이 문항 작업 등록', 'enqueueSimilarProblemTasks')
    .addSeparator()
    .addItem('큐 1회 처리', 'processQueue')
    .addItem('자동 처리 트리거 설치', 'installQueueTrigger')
    .addItem('자동 처리 트리거 삭제', 'removeQueueTriggers')
    .addToUi();
}

function setupSheets() {
  const ss = SpreadsheetApp.getActive();
  ensureSheet_(ss, SHEETS.PROBLEM_BANK, HEADERS.PROBLEM_BANK);
  ensureSheet_(ss, SHEETS.QUEUE, HEADERS.QUEUE);
  ensureSheet_(ss, SHEETS.ADMIN, HEADERS.ADMIN);
  ensureSheet_(ss, SHEETS.TWIN_RULES, HEADERS.TWIN_RULES);
  ensureSheet_(ss, SHEETS.API_LOG, HEADERS.API_LOG);
  ensureSheet_(ss, SHEETS.WRONG_HISTORY, HEADERS.WRONG_HISTORY);
  ensureSheet_(ss, SHEETS.WEAKNESS_SUMMARY, HEADERS.WEAKNESS_SUMMARY);
  ensureSheet_(ss, SHEETS.EXAM_LIST, HEADERS.EXAM_LIST);
  ensureSheet_(ss, SHEETS.TYPE_MAPPING, HEADERS.TYPE_MAPPING);
  refreshExamList();
  refreshTwinRuleDrafts(true);
  seedAdminExamples_(ss.getSheetByName(SHEETS.ADMIN));
  seedTwinRuleExamples_(ss.getSheetByName(SHEETS.TWIN_RULES));
  protectAndHideAdminSheets_(ss);
  SpreadsheetApp.getUi().alert('초기 시트 생성/정비가 완료되었습니다. 관리자_설정에 API 키와 Drive 루트 폴더 ID를 입력하세요.');
}

function installQueueTrigger() {
  removeQueueTriggers();
  ScriptApp.newTrigger('processQueue')
    .timeBased()
    .everyMinutes(1)
    .create();
  SpreadsheetApp.getUi().alert('processQueue 자동 처리 트리거를 1분 간격으로 설치했습니다.');
}

function removeQueueTriggers() {
  ScriptApp.getProjectTriggers()
    .filter(trigger => trigger.getHandlerFunction() === 'processQueue')
    .forEach(trigger => ScriptApp.deleteTrigger(trigger));
}

function refreshExamList() {
  const ss = SpreadsheetApp.getActive();
  const problemSheet = ss.getSheetByName(SHEETS.PROBLEM_BANK);
  const listSheet = ensureSheet_(ss, SHEETS.EXAM_LIST, HEADERS.EXAM_LIST);
  const problemHeaders = getHeaderMap_(problemSheet);
  const examNameColumn = problemHeaders['시험지 이름'];
  if (!examNameColumn) throw new Error('문제은행 시트에 시험지 이름 열이 없습니다.');

  const values = problemSheet.getLastRow() < 2
    ? []
    : problemSheet.getRange(2, examNameColumn, problemSheet.getLastRow() - 1, 1).getValues();
  const names = unique_(values.map(row => String(row[0] || '').trim()).filter(Boolean)).sort();

  clearSheetBody_(listSheet);
  if (names.length) {
    listSheet.getRange(2, 1, names.length, 1).setValues(names.map(name => [name]));
  }
  listSheet.hideSheet();
  return names.length;
}

function setupCurrentTeacherSheet() {
  const sheet = getActiveTeacherSheet_();
  const count = refreshExamList();
  applyExamDropdownToSheet_(sheet);
  SpreadsheetApp.getUi().alert(sheet.getName() + ' 시트 초기화가 완료되었습니다. 시험지 드롭다운 항목: ' + count + '개');
}

function refreshTwinRuleDrafts(silent) {
  const ss = SpreadsheetApp.getActive();
  const problemSheet = ss.getSheetByName(SHEETS.PROBLEM_BANK);
  const ruleSheet = ensureSheet_(ss, SHEETS.TWIN_RULES, HEADERS.TWIN_RULES);

  const existingRules = {};
  readObjects_(ruleSheet).forEach(item => {
    const type = String(item.rowObject['문제 유형'] || '').trim();
    if (type) existingRules[type] = true;
  });

  const mappings = readTypeMappings_();
  const types = unique_(readObjects_(problemSheet).map(item => {
    const row = item.rowObject;
    const standardType = String(row['표준 문제 유형'] || '').trim();
    if (standardType) return standardType;
    const rawType = String(row['문제 유형'] || '').trim();
    if (!rawType) return '';
    return getStandardType_(rawType, row['상위 단원'], row['하위 단원'], mappings);
  }).filter(Boolean)).sort();
  const newRows = types
    .filter(type => !existingRules[type])
    .map(type => [
      type,
      3,
      '중',
      type + ' 유형의 핵심 개념과 풀이 전략을 유지하되 숫자, 조건, 맥락을 바꾼 유사 문항을 생성한다.',
      '원문 문제의 숫자, 조건, 문장 구조를 그대로 복제하지 않는다.',
      'TRUE',
      'TRUE'
    ]);

  if (newRows.length) {
    ruleSheet.getRange(ruleSheet.getLastRow() + 1, 1, newRows.length, HEADERS.TWIN_RULES.length).setValues(newRows);
  }
  if (!silent) {
    SpreadsheetApp.getUi().alert(newRows.length + '개의 쌍둥이 규칙 초안을 추가했습니다.');
  }
  return newRows.length;
}

function refreshTypeMappingDrafts() {
  const ss = SpreadsheetApp.getActive();
  const problemSheet = ss.getSheetByName(SHEETS.PROBLEM_BANK);
  const mappingSheet = ensureSheet_(ss, SHEETS.TYPE_MAPPING, HEADERS.TYPE_MAPPING);
  const existing = {};
  readObjects_(mappingSheet).forEach(item => {
    const key = buildTypeMappingKey_(
      item.rowObject['원본 문제 유형'],
      item.rowObject['상위 단원'],
      item.rowObject['하위 단원']
    );
    existing[key] = true;
  });

  const newRows = [];
  readObjects_(problemSheet).forEach(item => {
    const row = item.rowObject;
    const rawType = String(row['문제 유형'] || '').trim();
    if (!rawType) return;
    const unit1 = String(row['상위 단원'] || '').trim();
    const unit2 = String(row['하위 단원'] || '').trim();
    const key = buildTypeMappingKey_(rawType, unit1, unit2);
    if (existing[key]) return;
    existing[key] = true;
    newRows.push([rawType, unit1, unit2, rawType, 'TRUE', '']);
  });

  if (newRows.length) {
    mappingSheet.getRange(mappingSheet.getLastRow() + 1, 1, newRows.length, HEADERS.TYPE_MAPPING.length).setValues(newRows);
  }
  SpreadsheetApp.getUi().alert(newRows.length + '개의 유형매핑 초안을 추가했습니다.');
  return newRows.length;
}

function applyTypeMappings() {
  const ss = SpreadsheetApp.getActive();
  const problemSheet = ss.getSheetByName(SHEETS.PROBLEM_BANK);
  ensureHeaderIncludes_(problemSheet, HEADERS.PROBLEM_BANK);
  const headers = getHeaderMap_(problemSheet);
  const mappings = readTypeMappings_();
  let updated = 0;

  readObjects_(problemSheet).forEach(item => {
    const rawType = String(item.rowObject['문제 유형'] || '').trim();
    if (!rawType) return;
    const unit1 = String(item.rowObject['상위 단원'] || '').trim();
    const unit2 = String(item.rowObject['하위 단원'] || '').trim();
    const standardType = getStandardType_(rawType, unit1, unit2, mappings);
    problemSheet.getRange(item.rowNumber, headers['표준 문제 유형']).setValue(standardType);
    updated += 1;
  });

  const ruleDraftCount = refreshTwinRuleDrafts(true);
  SpreadsheetApp.getUi().alert(updated + '개 문제행에 유형매핑을 적용했습니다. 쌍둥이 규칙 초안 추가: ' + ruleDraftCount + '개');
  return updated;
}

function enqueueProblemAnalysisTasks() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ss.getSheetByName(SHEETS.PROBLEM_BANK);
  if (!sheet) throw new Error('문제은행 시트가 없습니다. setupSheets()를 먼저 실행하세요.');

  const rows = readObjects_(sheet);
  const grouped = {};
  rows.forEach(item => {
    if (!item.rowObject['시험지 이름'] || !item.rowObject['문제번호'] || !item.rowObject['링크']) return;
    if (item.rowObject['문제 유형'] && item.rowObject['정답']) return;

    const key = String(item.rowObject['시험지 이름']) + '||' + String(item.rowObject['링크']);
    if (!grouped[key]) {
      grouped[key] = {
        examName: item.rowObject['시험지 이름'],
        fileUrl: item.rowObject['링크'],
        problemRows: []
      };
    }
    grouped[key].problemRows.push({
      rowNumber: item.rowNumber,
      problemNumber: normalizeProblemNumber_(item.rowObject['문제번호'])
    });
  });

  const queueItems = Object.keys(grouped).map(key => {
    const payload = grouped[key];
    return {
      taskType: TASK_TYPES.PROBLEM_ANALYSIS,
      targetSheet: SHEETS.PROBLEM_BANK,
      targetRow: payload.problemRows[0].rowNumber,
      payload
    };
  });

  const enqueuedCount = enqueueTasks_(queueItems);
  SpreadsheetApp.getUi().alert(enqueuedCount + '개의 문제은행 분석 작업을 등록했습니다.');
}

function enqueueStudentReportTasks() {
  const sheet = getActiveTeacherSheet_();
  const rows = readObjects_(sheet);
  const queueItems = rows
    .filter(item => item.rowObject['학생 이름'] && item.rowObject['시험지 이름'] && item.rowObject['틀린 문제 번호'])
    .filter(item => !item.rowObject['분석 보고서'])
    .map(item => ({
      taskType: TASK_TYPES.STUDENT_REPORT,
      targetSheet: sheet.getName(),
      targetRow: item.rowNumber,
      payload: {
        studentName: item.rowObject['학생 이름'],
        examName: item.rowObject['시험지 이름'],
        wrongNumbersText: item.rowObject['틀린 문제 번호']
      }
    }));

  const enqueuedCount = enqueueTasks_(queueItems);
  SpreadsheetApp.getUi().alert(enqueuedCount + '개의 분석 보고서 작업을 등록했습니다.');
}

function enqueueSimilarProblemTasks() {
  const sheet = getActiveTeacherSheet_();
  const rows = readObjects_(sheet);
  const queueItems = rows
    .filter(item => item.rowObject['학생 이름'] && item.rowObject['시험지 이름'] && item.rowObject['틀린 문제 번호'])
    .filter(item => !item.rowObject['쌍둥이 문항'])
    .map(item => ({
      taskType: TASK_TYPES.SIMILAR_PROBLEMS,
      targetSheet: sheet.getName(),
      targetRow: item.rowNumber,
      payload: {
        studentName: item.rowObject['학생 이름'],
        examName: item.rowObject['시험지 이름'],
        wrongNumbersText: item.rowObject['틀린 문제 번호']
      }
    }));

  const enqueuedCount = enqueueTasks_(queueItems);
  SpreadsheetApp.getUi().alert(enqueuedCount + '개의 쌍둥이 문항 작업을 등록했습니다.');
}

function processQueue() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;

  try {
    const ss = SpreadsheetApp.getActive();
    const queueSheet = ss.getSheetByName(SHEETS.QUEUE);
    if (!queueSheet) throw new Error('작업큐 시트가 없습니다.');

    const queue = readObjects_(queueSheet);
    const now = new Date();
    recoverStaleRunningQueueItems_(queueSheet, queue, now);
    const runnable = selectRunnableQueueItems_(queue, now, getGlobalBatchSize_());

    runnable.forEach(item => processQueueItem_(queueSheet, item));
  } finally {
    lock.releaseLock();
  }
}

function recoverStaleRunningQueueItems_(queueSheet, queue, now) {
  const headers = getHeaderMap_(queueSheet);
  (queue || [])
    .filter(item => item.rowObject['상태'] === QUEUE_STATUS.RUNNING)
    .filter(item => isStaleRunningQueueItem_(item, now))
    .forEach(item => {
      const nextRetryCount = Number(item.rowObject['재시도횟수'] || 0) + 1;
      const nextStatus = nextRetryCount >= MAX_RETRIES ? QUEUE_STATUS.FAILED : QUEUE_STATUS.PENDING;
      setRowValues_(queueSheet, item.rowNumber, headers, {
        '상태': nextStatus,
        '재시도횟수': nextRetryCount,
        '예약시각': nextStatus === QUEUE_STATUS.PENDING ? now : '',
        '오류메시지': '오래된 RUNNING 작업을 자동 복구했습니다. 이전 실행이 시간 초과로 중단되었을 수 있습니다.',
        '처리시간': now
      });
      item.rowObject['상태'] = nextStatus;
      item.rowObject['재시도횟수'] = nextRetryCount;
      item.rowObject['예약시각'] = nextStatus === QUEUE_STATUS.PENDING ? now : '';
      item.rowObject['오류메시지'] = '오래된 RUNNING 작업을 자동 복구했습니다. 이전 실행이 시간 초과로 중단되었을 수 있습니다.';
      item.rowObject['처리시간'] = now;
    });
}

function isStaleRunningQueueItem_(item, now) {
  const processedAt = item && item.rowObject ? item.rowObject['처리시간'] : '';
  if (!processedAt) return true;
  const processedTime = new Date(processedAt).getTime();
  if (!processedTime || isNaN(processedTime)) return true;
  return now.getTime() - processedTime >= STALE_RUNNING_QUEUE_MS;
}

function selectRunnableQueueItems_(queue, now, maxItems) {
  const selected = [];
  const selectedSheets = {};

  queue
      .filter(item => item.rowObject['상태'] === QUEUE_STATUS.PENDING)
      .filter(item => !item.rowObject['예약시각'] || new Date(item.rowObject['예약시각']).getTime() <= now.getTime())
      .forEach(item => {
        const sheetName = String(item.rowObject['대상시트'] || '');
        if (!sheetName) return;
        if (selectedSheets[sheetName]) return;
        if (isTeacherTask_(item.rowObject['작업종류']) && !isSheetCooldownReady_(sheetName, now)) return;
        selected.push(item);
        selectedSheets[sheetName] = true;
      });

  return selected.slice(0, maxItems);
}

function processQueueItem_(queueSheet, item) {
  const queueHeaders = getHeaderMap_(queueSheet);
  const taskType = item.rowObject['작업종류'];
  const retryCount = Number(item.rowObject['재시도횟수'] || 0);

  setRowValues_(queueSheet, item.rowNumber, queueHeaders, {
    '상태': QUEUE_STATUS.RUNNING,
    '처리시간': new Date(),
    '오류메시지': ''
  });

  try {
    const payload = JSON.parse(item.rowObject['페이로드JSON'] || '{}');
    if (shouldSkipCompletedTask_(item.rowObject, payload)) {
      setRowValues_(queueSheet, item.rowNumber, queueHeaders, {
        '상태': QUEUE_STATUS.DONE,
        '처리시간': new Date(),
        '오류메시지': '이미 결과 셀이 채워져 있어 스킵했습니다.'
      });
      return;
    }

    if (taskType === TASK_TYPES.PROBLEM_ANALYSIS) {
      handleProblemAnalysis_(payload);
    } else if (taskType === TASK_TYPES.STUDENT_REPORT) {
      handleStudentReport_(item.rowObject['대상시트'], Number(item.rowObject['대상행']), payload);
    } else if (taskType === TASK_TYPES.SIMILAR_PROBLEMS) {
      handleSimilarProblems_(item.rowObject['대상시트'], Number(item.rowObject['대상행']), payload);
    } else {
      throw new Error('알 수 없는 작업종류: ' + taskType);
    }

    setRowValues_(queueSheet, item.rowNumber, queueHeaders, {
      '상태': QUEUE_STATUS.DONE,
      '처리시간': new Date(),
      '오류메시지': ''
    });
    if (isTeacherTask_(taskType)) {
      markSheetProcessed_(item.rowObject['대상시트']);
    }
  } catch (err) {
    if (err && err.deferOnly) {
      setRowValues_(queueSheet, item.rowNumber, queueHeaders, {
        '상태': QUEUE_STATUS.PENDING,
        '예약시각': new Date(Date.now() + (err.deferMs || 60 * 1000)),
        '처리시간': new Date(),
        '오류메시지': String(err.message || err).slice(0, 1000)
      });
      return;
    }

    const nextRetryCount = retryCount + 1;
    const nextStatus = nextRetryCount >= MAX_RETRIES ? QUEUE_STATUS.FAILED : QUEUE_STATUS.PENDING;
    const nextReservation = new Date(Date.now() + Math.pow(2, nextRetryCount) * 60 * 1000);
    setRowValues_(queueSheet, item.rowNumber, queueHeaders, {
      '상태': nextStatus,
      '재시도횟수': nextRetryCount,
      '예약시각': nextStatus === QUEUE_STATUS.PENDING ? nextReservation : '',
      '처리시간': new Date(),
      '오류메시지': String(err && err.message ? err.message : err).slice(0, 1000)
    });
  }
}

function handleProblemAnalysis_(payload) {
  const ss = SpreadsheetApp.getActive();
  const sheet = ss.getSheetByName(SHEETS.PROBLEM_BANK);
  const headers = getHeaderMap_(sheet);

  const numbers = payload.problemRows.map(row => row.problemNumber).join(', ');
  const prompt = [
    '너는 중고등학교 수학 시험지를 정확하게 풀이하고 검산하는 베테랑 수학교사다.',
    '첨부된 한 페이지에서 다음 문제번호만 분석하라: ' + numbers,
    '각 문제에 대해 반드시 문제를 끝까지 풀고, 정답을 검산한 뒤 문제 유형, 상위 단원, 하위 단원, 정답을 작성하라.',
    '정답을 추측하지 말라. 풀이 근거가 부족하거나 이미지 판독이 불확실하면 confidence를 MEDIUM 또는 LOW로 낮추고 reviewReason에 이유를 적어라.',
    '도형, 그래프, 길이, 넓이, 각도, 단위(cm, m 등), 분수/무리수 답은 특히 조건을 다시 확인하고 검산하라.',
    '문제에서 요구하는 단위와 답 형식에 맞게 최종 정답을 정리하라.',
    'solutionSummary에는 핵심 풀이와 검산 근거를 1~2문장으로 적어라.',
    'solutionSummary는 구글 시트 셀에서 바로 읽을 수 있는 평문으로 작성하라. LaTeX, 마크다운, $...$, \\( ... \\), \\frac, \\sqrt 같은 표기를 쓰지 말고 x², √3, 3/4처럼 일반 텍스트와 유니코드 기호로 적어라.',
    'confidence는 HIGH, MEDIUM, LOW 중 하나만 사용하라.',
    'reviewReason은 사람이 확인해야 할 이유가 있을 때만 적고, 확실하면 빈 문자열로 둔다.',
    '반드시 JSON 배열만 반환하라. 설명, 마크다운, 코드블록은 금지.',
    '형식: [{"problemNumber":"1","type":"삼각형의 닮음과 길이","unit1":"도형","unit2":"닮음","answer":"9cm","solutionSummary":"닮음비를 이용해 대응변의 길이를 구하고 단위를 확인하면 9cm이다.","confidence":"HIGH","reviewReason":""}]',
    '문제를 찾을 수 없으면 type, unit1, unit2, answer, solutionSummary를 빈 문자열로 두고 confidence는 LOW, reviewReason은 "문제를 찾지 못함"으로 둔다.',
    '분류 기준:',
    '- unit1은 큰 단원명으로 짧게 적어라. 예: 함수, 방정식, 도형, 수와 식',
    '- unit2는 교과서 소단원명으로 적어라. 예: 일차함수와 그래프, 연립일차방정식, 이차함수, 원의 넓이',
    '- type은 학생의 약점 유형으로 쓸 구체 행동명으로 적어라.',
    '- type은 너무 넓은 단원명만 쓰지 말고, "무엇을 이용해 무엇을 구하는지"가 드러나게 적어라.',
    '- type은 12~25자 정도의 간결한 명사구로 적어라.',
    '- type에는 "이차함수 활용", "이차함수 그래프", "이차방정식 활용"처럼 단원명+활용/그래프만 있는 넓은 표현을 쓰지 말고, 반드시 조건, 도구, 구해야 할 값을 포함하라.',
    '- 나쁜 type 예: 이차함수 활용, 이차함수 그래프, 이차방정식 활용',
    '- 좋은 type 예: 완전제곱식으로 이차방정식 풀기, 그래프 교점으로 해의 개수 판단, 이차함수로 도형 넓이 계산',
    '- 같은 의미의 유형은 같은 표현으로 통일하라.'
  ].join('\n');

  const filePart = buildGeminiFilePart_(payload.fileUrl);
  const response = callGemini_(TASK_TYPES.PROBLEM_ANALYSIS, '', prompt, [filePart]);
  const parsed = parseJsonArray_(response.text);
  const byNumber = {};
  parsed.forEach(item => {
    byNumber[normalizeProblemNumber_(item.problemNumber)] = item;
  });

  payload.problemRows.forEach(row => {
    const result = byNumber[normalizeProblemNumber_(row.problemNumber)] || {};
    const confidence = normalizeConfidence_(result.confidence);
    const reviewReason = String(result.reviewReason || '').trim();
    const hasResult = Boolean(result.type || result.answer);
    const needsReview = hasResult && (confidence !== 'HIGH' || reviewReason);
    const updates = {
      '처리상태': hasResult ? (needsReview ? 'REVIEW' : 'DONE') : 'NO_RESULT',
      '오류메시지': hasResult ? '' : 'AI 응답에서 해당 문제번호를 찾지 못했습니다.',
      '마지막처리시간': new Date()
    };
    if (result.type) updates['문제 유형'] = result.type;
    if (result.unit1) updates['상위 단원'] = result.unit1;
    if (result.unit2) updates['하위 단원'] = result.unit2;
    if (result.type) updates['표준 문제 유형'] = getStandardType_(result.type, result.unit1, result.unit2);
    if (result.answer) updates['정답'] = result.answer;
    if (result.solutionSummary) updates['풀이요약'] = result.solutionSummary;
    if (confidence) updates['신뢰도'] = confidence;
    updates['검산메모'] = reviewReason || (needsReview ? '신뢰도 ' + confidence + '로 사람 확인이 필요합니다.' : '');
    setRowValues_(sheet, row.rowNumber, headers, updates);
  });
}

function handleStudentReport_(targetSheetName, targetRow, payload) {
  const ss = SpreadsheetApp.getActive();
  const teacherSheet = getQueueTargetSheet_(ss, targetSheetName, payload, '분석 보고서 작업 대상');
  const teacherHeaders = getHeaderMap_(teacherSheet);
  const wrongProblems = lookupWrongProblems_(payload.examName, payload.wrongNumbersText);
  const historySummary = buildStudentHistorySummary_(payload.studentName, wrongProblems);

  const prompt = buildReportPrompt_(payload.studentName, payload.examName, wrongProblems, historySummary);
  const response = callGemini_(TASK_TYPES.STUDENT_REPORT, targetSheetName, prompt, []);
  const fileUrl = saveTextToStudentFolder_(
    payload.studentName,
    sanitizeFileName_(payload.examName + ' 보고서.txt'),
    response.text
  );
  upsertWrongHistory_(targetSheetName, targetRow, payload.studentName, payload.examName, wrongProblems, {
    reportUrl: fileUrl
  });

  setRowValues_(teacherSheet, targetRow, teacherHeaders, {
    '분석 보고서': fileUrl,
    '누적 분석 보고서': fileUrl,
    '처리상태': 'DONE',
    '오류메시지': ''
  });
}

function handleSimilarProblems_(targetSheetName, targetRow, payload) {
  const ss = SpreadsheetApp.getActive();
  const teacherSheet = getQueueTargetSheet_(ss, targetSheetName, payload, '쌍둥이 문항 작업 대상');
  const teacherHeaders = getHeaderMap_(teacherSheet);
  const rowObject = readRowObject_(teacherSheet, targetRow);
  const wrongProblems = lookupWrongProblems_(payload.examName, payload.wrongNumbersText);
  if (!wrongProblems.length) {
    throw new Error('오답이 없는 100점 기록에는 쌍둥이 문항을 생성할 수 없습니다.');
  }
  const rulesByType = readTwinRules_();
  const missingTypes = unique_(wrongProblems.map(item => item.type).filter(type => !rulesByType[type]));
  if (missingTypes.length) {
    throw new Error('쌍둥이_규칙 시트에 규칙이 없는 문제 유형: ' + missingTypes.join(', '));
  }

  const reportText = readDriveTextFromUrl_(rowObject['분석 보고서']);
  const plan = buildTwinGenerationPlan_(wrongProblems, payload.examName, targetSheetName);
  const generatedProblems = generateSimilarProblemsWithPool_(
    targetSheetName,
    payload.studentName,
    payload.examName,
    wrongProblems,
    reportText,
    rulesByType,
    plan
  );
  const finalText = formatGeneratedProblems_(payload.studentName, payload.examName, plan, generatedProblems);
  const fileUrl = saveTextToStudentFolder_(
    payload.studentName,
    sanitizeFileName_(payload.examName + ' 쌍둥이문항.txt'),
    finalText
  );
  upsertWrongHistory_(targetSheetName, targetRow, payload.studentName, payload.examName, wrongProblems, {
    twinUrl: fileUrl
  });

  setRowValues_(teacherSheet, targetRow, teacherHeaders, {
    '쌍둥이 문항': fileUrl,
    '처리상태': hasGeneratedProblemReviewItems_(generatedProblems) ? 'REVIEW' : 'DONE',
    '오류메시지': hasGeneratedProblemReviewItems_(generatedProblems) ? '쌍둥이 문항 일부에 [검수 필요] 표시가 있습니다.' : ''
  });
}

function callGemini_(feature, sheetScope, prompt, extraParts) {
  const keyConfig = pickAvailableKey_(feature, sheetScope, estimateTokens_(prompt));
  if (!keyConfig) {
    throwDefer_(feature + ' 기능에 사용 가능한 API 프로젝트 quota가 없습니다. 다음 트리거에서 재시도됩니다.');
  }

  const adjustedTokens = estimateRequestTokens_(prompt, extraParts, keyConfig);
  if (!isWithinQuota_(keyConfig, adjustedTokens)) {
    throwDefer_(keyConfig.projectName + ' 프로젝트 quota가 부족하여 다음 트리거로 연기합니다.');
  }

  return callGeminiWithConfig_(feature, keyConfig, prompt, extraParts);
}

function callGeminiWithConfig_(feature, keyConfig, prompt, extraParts) {
  const model = normalizeModelName_(keyConfig.model || DEFAULT_MODEL);
  const url = 'https://generativelanguage.googleapis.com/v1beta/models/' +
    encodeURIComponent(model) + ':generateContent?key=' + encodeURIComponent(keyConfig.apiKey);

  const parts = [{ text: prompt }].concat(extraParts || []);
  const body = {
    contents: [{ role: 'user', parts }],
    generationConfig: {
      temperature: feature === TASK_TYPES.PROBLEM_ANALYSIS ? 0.1 : 0.5
    }
  };

  const response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(body),
    muteHttpExceptions: true
  });

  const statusCode = response.getResponseCode();
  const raw = response.getContentText();
  if (keyConfig.delayMs > 0) Utilities.sleep(keyConfig.delayMs);

  if (statusCode < 200 || statusCode >= 300) {
    const message = 'Gemini API 오류 HTTP ' + statusCode + ': ' + raw.slice(0, 500);
    logApiUse_(feature, keyConfig, estimateRequestTokens_(prompt, extraParts, keyConfig), 'ERROR', raw.slice(0, 500));
    if (statusCode === 429) {
      markProjectCooldown_(keyConfig, raw);
    }
    if (isTemporaryHttpError_(statusCode)) {
      throwDefer_(message, 15 * 60 * 1000);
    }
    throw new Error(message);
  }

  const json = JSON.parse(raw);
  const text = extractGeminiText_(json);
  logApiUse_(
    feature,
    keyConfig,
    estimateRequestTokens_(prompt, extraParts, keyConfig),
    'OK',
    '',
    json.usageMetadata || {}
  );
  return { text, raw: json, usageMetadata: json.usageMetadata || {} };
}

function callGeminiBatch_(feature, requestItems) {
  const fetchRequests = requestItems.map(item => {
    const model = normalizeModelName_(item.keyConfig.model || DEFAULT_MODEL);
    const url = 'https://generativelanguage.googleapis.com/v1beta/models/' +
      encodeURIComponent(model) + ':generateContent?key=' + encodeURIComponent(item.keyConfig.apiKey);
    const parts = [{ text: item.prompt }].concat(item.extraParts || []);
    return {
      url,
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify({
        contents: [{ role: 'user', parts }],
        generationConfig: { temperature: feature === TASK_TYPES.PROBLEM_ANALYSIS ? 0.1 : 0.5 }
      }),
      muteHttpExceptions: true
    };
  });

  const responses = UrlFetchApp.fetchAll(fetchRequests);
  const results = [];
  let temporaryErrorMessage = '';
  let fatalErrorMessage = '';

  responses.forEach((response, index) => {
    const item = requestItems[index];
    const statusCode = response.getResponseCode();
    const raw = response.getContentText();
    if (statusCode < 200 || statusCode >= 300) {
      const message = 'Gemini API 오류 HTTP ' + statusCode + ': ' + raw.slice(0, 500);
      logApiUse_(feature, item.keyConfig, estimateRequestTokens_(item.prompt, item.extraParts, item.keyConfig), 'ERROR', raw.slice(0, 500));
      if (statusCode === 429) {
        markProjectCooldown_(item.keyConfig, raw);
      }
      if (isTemporaryHttpError_(statusCode)) {
        if (!temporaryErrorMessage) temporaryErrorMessage = message;
        return;
      }
      if (!fatalErrorMessage) fatalErrorMessage = message;
      return;
    }

    const json = JSON.parse(raw);
    const text = extractGeminiText_(json);
    logApiUse_(
      feature,
      item.keyConfig,
      estimateRequestTokens_(item.prompt, item.extraParts, item.keyConfig),
      'OK',
      '',
      json.usageMetadata || {}
    );
    results.push({ text, raw: json, usageMetadata: json.usageMetadata || {} });
  });

  if (temporaryErrorMessage) {
    throwDefer_(temporaryErrorMessage, 15 * 60 * 1000);
  }
  if (fatalErrorMessage) {
    throw new Error(fatalErrorMessage);
  }
  return results;
}

function pickAvailableKey_(feature, sheetScope, estimatedTokens) {
  const configs = pickAvailableKeys_(feature, sheetScope, estimatedTokens);
  if (!configs.length) return null;
  if (feature === TASK_TYPES.PROBLEM_ANALYSIS) {
    return pickRoundRobinConfig_(feature, sheetScope, configs);
  }
  return configs[0];
}

function pickAvailableKeys_(feature, sheetScope, estimatedTokens) {
  return readAdminConfigs_()
    .filter(row => row.feature === feature)
    .filter(row => row.enabled)
    .filter(row => matchesSheetScope_(row.sheetScope, sheetScope))
    .filter(row => !isProjectCooldownActive_(row))
    .filter(row => isWithinQuota_(row, estimatedTokens));
}

function pickSimilarProblemSetKeys_(sheetScope, estimatedTokens, reuseCurrentSet) {
  const configs = pickAvailableKeys_(TASK_TYPES.SIMILAR_PROBLEMS, sheetScope, estimatedTokens);
  if (!configs.length) return [];

  const grouped = groupConfigsBySet_(configs);
  const setNames = Object.keys(grouped).sort();
  if (setNames.length <= 1) return configs;

  const props = PropertiesService.getScriptProperties();
  const key = buildSimilarProblemSetPropertyKey_(sheetScope);
  let selectedSet = props.getProperty(key);

  if (!reuseCurrentSet || !selectedSet || !grouped[selectedSet] || !grouped[selectedSet].length) {
    selectedSet = pickNextConfigSetName_(setNames, selectedSet);
    props.setProperty(key, selectedSet);
  }

  return grouped[selectedSet] || configs;
}

function groupConfigsBySet_(configs) {
  const grouped = {};
  configs.forEach(config => {
    const setName = getConfigSetName_(config);
    if (!grouped[setName]) grouped[setName] = [];
    grouped[setName].push(config);
  });
  return grouped;
}

function getConfigSetName_(config) {
  const name = String((config && config.projectName) || '').trim();
  const match = name.match(/^([A-Za-z0-9]+)[-_]/);
  return match ? match[1].toUpperCase() : 'DEFAULT';
}

function pickNextConfigSetName_(setNames, currentSet) {
  if (!currentSet) return setNames[0];
  const currentIndex = setNames.indexOf(currentSet);
  return setNames[(currentIndex + 1) % setNames.length];
}

function buildSimilarProblemSetPropertyKey_(sheetScope) {
  return 'SIMILAR_PROBLEM_SET__' + String(sheetScope || '*').trim();
}

function isWithinQuota_(config, estimatedTokens) {
  const ss = SpreadsheetApp.getActive();
  const logSheet = ss.getSheetByName(SHEETS.API_LOG);
  const logs = readObjects_(logSheet).map(item => item.rowObject);
  const now = Date.now();
  const today = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
  const projectLogs = logs.filter(log => log['프로젝트명'] === config.projectName && log['상태'] === 'OK');
  const minuteLogs = projectLogs.filter(log => now - new Date(log['시간']).getTime() < 60 * 1000);
  const todayLogs = projectLogs.filter(log => String(log['날짜']) === today);
  const rpmUsed = sum_(minuteLogs.map(log => Number(log['요청수'] || 0)));
  const tpmUsed = sum_(minuteLogs.map(log => Number(log['예상입력토큰'] || 0)));
  const rpdUsed = sum_(todayLogs.map(log => Number(log['요청수'] || 0)));
  return rpmUsed + 1 <= config.rpm &&
    tpmUsed + estimatedTokens <= config.tpm &&
    rpdUsed + 1 <= config.rpd;
}

function markProjectCooldown_(config, rawErrorText) {
  const retrySeconds = extractRetrySeconds_(rawErrorText);
  const cooldownMs = retrySeconds
    ? Math.ceil((retrySeconds + 20) * 1000)
    : 15 * 60 * 1000;
  const until = Date.now() + cooldownMs;
  PropertiesService.getScriptProperties().setProperty(buildProjectCooldownKey_(config), String(until));
}

function isProjectCooldownActive_(config) {
  const until = Number(PropertiesService.getScriptProperties().getProperty(buildProjectCooldownKey_(config)) || 0);
  return until && Date.now() < until;
}

function buildProjectCooldownKey_(config) {
  return [
    'PROJECT_COOLDOWN_UNTIL',
    config && config.feature,
    config && config.sheetScope,
    config && config.projectName
  ].map(value => String(value || '').trim()).join('__');
}

function extractRetrySeconds_(text) {
  const match = String(text || '').match(/retry\s+in\s+([0-9.]+)s/i);
  return match ? Number(match[1]) : 0;
}

function readAdminConfigs_() {
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEETS.ADMIN);
  return readObjects_(sheet)
    .map(item => item.rowObject)
    .filter(row => row['기능'] && row['프로젝트명'] && row['API키'])
    .map(row => ({
      feature: normalizeFeatureName_(row['기능']),
      sheetScope: String(row['적용시트'] || '').trim(),
      projectName: String(row['프로젝트명']).trim(),
      apiKey: String(row['API키']).trim(),
      rpm: Number(row['RPM'] || 10),
      tpm: Number(row['TPM'] || 250000),
      rpd: Number(row['RPD'] || 250),
      model: String(row['모델명'] || DEFAULT_MODEL).trim(),
      batchSize: Number(row['1회처리개수'] || DEFAULT_BATCH_SIZE),
      delayMs: Number(row['요청간대기ms'] || DEFAULT_REQUEST_DELAY_MS),
      attachmentTokenBudget: Number(row['첨부토큰보정값'] || defaultAttachmentTokenBudget_(normalizeFeatureName_(row['기능']))),
      outputTokenBudget: Number(row['출력토큰보정값'] || defaultOutputTokenBudget_(normalizeFeatureName_(row['기능']))),
      driveRootFolderId: String(row['Drive루트폴더ID'] || '').trim(),
      generalProblemCount: Number(row['일반문항수'] || 0),
      imageProblemCount: Number(row['이미지문항수'] || 0),
      enabled: String(row['사용여부'] || 'TRUE').toUpperCase() !== 'FALSE'
    }));
}

function buildGeminiFilePart_(fileUrl) {
  const fileId = extractDriveFileId_(fileUrl);
  if (!fileId) throw new Error('Drive 파일 ID를 링크에서 찾을 수 없습니다: ' + fileUrl);

  const blob = DriveApp.getFileById(fileId).getBlob();
  return {
    inlineData: {
      mimeType: blob.getContentType(),
      data: Utilities.base64Encode(blob.getBytes())
    }
  };
}

function buildReportPrompt_(studentName, examName, wrongProblems, historySummary) {
  return [
    '너는 중고등학교 수학학원에서 학부모 상담용 보고서를 작성하는 교사다.',
    '문체는 친절하고 구체적이되 과장하지 말라.',
    '학생명: ' + studentName,
    '시험명: ' + examName,
    '이번 시험 결과: ' + (wrongProblems.length ? '오답 있음' : PERFECT_SCORE_TEXT),
    '오답 목록(JSON): ' + JSON.stringify(wrongProblems),
    '누적 오답 요약(JSON): ' + JSON.stringify(historySummary || {}),
    '',
    '다음 순서로 텍스트 보고서를 작성하라.',
    '1. 전체 요약',
    '2. 주요 약점 유형',
    '3. 유형별 해설과 원인 추정',
    '4. 누적 기록 기준의 개선/반복 약점',
    '5. 다음 주 학습 방향',
    '6. 가정에서 확인할 과제 제안',
    '누적 기록이 충분하지 않으면 이번 시험 기준 분석이라고 명시하라.',
    '',
    '마크다운 표는 쓰지 말고 일반 txt 문서처럼 작성하라.'
  ].join('\n');
}

function generateSimilarProblemsWithPool_(targetSheetName, studentName, examName, wrongProblems, reportText, rulesByType, plan) {
  const samplePrompt = buildSimilarProblemsPrompt_(
    studentName,
    examName,
    wrongProblems,
    reportText,
    rulesByType,
    plan.items.slice(0, Math.max(1, Math.ceil(plan.items.length / 5)))
  );
  const availableKeys = pickSimilarProblemSetKeys_(targetSheetName, estimateTokens_(samplePrompt));
  if (!availableKeys.length) {
    throwDefer_(targetSheetName + ' 시트의 문제생성기 키 풀에 사용 가능한 프로젝트 quota가 없습니다.');
  }

  const chunks = chunkByMaxSize_(plan.items, 3);
  if (chunks.length > availableKeys.length) {
    throwDefer_(targetSheetName + ' 시트의 문제생성기 사용 가능 프로젝트가 부족합니다. 3문항/요청 병렬 생성을 위해 ' + chunks.length + '개가 필요합니다.');
  }

  const requests = chunks.map((chunk, index) => {
    const keyConfig = availableKeys[index];
    const prompt = buildSimilarProblemsPrompt_(studentName, examName, wrongProblems, reportText, rulesByType, chunk);
    if (!isWithinQuota_(keyConfig, estimateRequestTokens_(prompt, [], keyConfig))) {
      throwDefer_(keyConfig.projectName + ' 프로젝트 quota가 부족하여 다음 트리거로 연기합니다.');
    }
    return { keyConfig, prompt, extraParts: [], planItems: chunk };
  });

  let generated = [];
  const responses = callGeminiBatch_(TASK_TYPES.SIMILAR_PROBLEMS, requests);
  responses.forEach((response, index) => {
    generated = generated.concat(parseGeneratedProblemArray_(response.text, requests[index].planItems));
  });
  if (!generated.length) {
    throw new Error('쌍둥이 문항 응답을 파싱하지 못했습니다. Gemini 응답 형식이 구분자 형식과 맞지 않습니다.');
  }
  return retryAndValidateGeneratedProblems_(
    targetSheetName,
    studentName,
    examName,
    wrongProblems,
    reportText,
    rulesByType,
    plan,
    generated
  );
}

function retryAndValidateGeneratedProblems_(targetSheetName, studentName, examName, wrongProblems, reportText, rulesByType, plan, generated) {
  let current = generated.slice();
  for (let attempt = 0; attempt < 1; attempt++) {
    const issues = findGeneratedProblemIssues_(plan, current);
    if (!issues.retryNumbers.length) return current;

    const retrySet = {};
    issues.retryNumbers.forEach(number => retrySet[number] = true);
    const retryItems = plan.items.filter(item => retrySet[Number(item.number)]);
    const retryGenerated = requestSimilarProblemRetries_(
      targetSheetName,
      studentName,
      examName,
      wrongProblems,
      reportText,
      rulesByType,
      retryItems
    );
    current = mergeGeneratedProblemRetries_(current, retryGenerated, retrySet);
  }

  const finalIssues = findGeneratedProblemIssues_(plan, current);
  return finalIssues.retryNumbers.length
    ? fillGeneratedProblemReviewPlaceholders_(plan, current, finalIssues)
    : current;
}

function requestSimilarProblemRetries_(targetSheetName, studentName, examName, wrongProblems, reportText, rulesByType, retryItems) {
  if (!retryItems.length) return [];
  const samplePrompt = buildSimilarProblemsPrompt_(studentName, examName, wrongProblems, reportText, rulesByType, retryItems);
  const availableKeys = pickSimilarProblemSetKeys_(targetSheetName, estimateTokens_(samplePrompt), true);
  const chunks = chunkByMaxSize_(retryItems, 3);
  if (!availableKeys.length || chunks.length > availableKeys.length) {
    throwDefer_(targetSheetName + ' 시트의 문제생성기 재시도에 사용 가능한 프로젝트 quota가 부족합니다.');
  }

  const requests = chunks.map((chunk, index) => {
    const keyConfig = availableKeys[index];
    const prompt = buildSimilarProblemsPrompt_(studentName, examName, wrongProblems, reportText, rulesByType, chunk);
    if (!isWithinQuota_(keyConfig, estimateRequestTokens_(prompt, [], keyConfig))) {
      throwDefer_(keyConfig.projectName + ' 프로젝트 quota가 부족하여 다음 트리거로 연기합니다.');
    }
    return { keyConfig, prompt, extraParts: [], planItems: chunk };
  });

  let generated = [];
  const responses = callGeminiBatch_(TASK_TYPES.SIMILAR_PROBLEMS, requests);
  responses.forEach((response, index) => {
    generated = generated.concat(parseGeneratedProblemArray_(response.text, requests[index].planItems));
  });
  return generated;
}

function findGeneratedProblemIssues_(plan, generated) {
  const generatedByNumber = {};
  generated.forEach(item => {
    generatedByNumber[Number(item.number)] = item;
  });
  const planNumbers = plan.items.map(item => Number(item.number));
  const planByNumber = {};
  plan.items.forEach(item => {
    planByNumber[Number(item.number)] = item;
  });
  const missingNumbers = planNumbers.filter(number => !generatedByNumber[number]);
  const incompleteNumbers = planNumbers.filter(number => {
    const generatedItem = generatedByNumber[number] || {};
    return !String(generatedItem.problem || generatedItem.body || '').trim()
      || !String(generatedItem.answer || '').trim()
      || !String(generatedItem.solution || '').trim();
  });
  const draftLeakNumbers = planNumbers.filter(number => hasDraftLeakText_(generatedByNumber[number], planByNumber[number]));
  const formMismatchNumbers = planNumbers.filter(number => hasGeneratedProblemFormMismatch_(generatedByNumber[number], planByNumber[number]));
  const missingRequiredImageNumbers = planNumbers.filter(number => {
    const planItem = planByNumber[number] || {};
    return planItem.imageRequired && !hasImageTags_(generatedByNumber[number]);
  });
  const imageSpecIssueNumbers = planNumbers.filter(number => hasBadImageSpec_(generatedByNumber[number], planByNumber[number]));
  return {
    missingNumbers,
    incompleteNumbers,
    draftLeakNumbers,
    formMismatchNumbers,
    missingRequiredImageNumbers,
    imageSpecIssueNumbers,
    retryNumbers: unique_(missingNumbers.concat(incompleteNumbers).concat(draftLeakNumbers).concat(formMismatchNumbers).concat(missingRequiredImageNumbers).concat(imageSpecIssueNumbers))
  };
}

function mergeGeneratedProblemRetries_(current, retryGenerated, retrySet) {
  const kept = current.filter(item => !retrySet[Number(item.number)]);
  return kept.concat(retryGenerated);
}

function fillGeneratedProblemReviewPlaceholders_(plan, generated, issues) {
  const generatedByNumber = {};
  generated.forEach(item => {
    generatedByNumber[Number(item.number)] = item;
  });
  const missingSet = toNumberSet_(issues.missingNumbers);
  const incompleteSet = toNumberSet_(issues.incompleteNumbers);
  const draftLeakSet = toNumberSet_(issues.draftLeakNumbers);
  const formMismatchSet = toNumberSet_(issues.formMismatchNumbers);
  const missingRequiredImageSet = toNumberSet_(issues.missingRequiredImageNumbers);
  const imageSpecIssueSet = toNumberSet_(issues.imageSpecIssueNumbers);

  return plan.items.map(item => {
    const number = Number(item.number);
    const existing = generatedByNumber[number] || {};
    if (missingSet[number]) {
      return buildReviewProblemItem_(number, item, 'AI 응답에서 해당 번호를 찾지 못했습니다.');
    }
    if (draftLeakSet[number]) {
      return buildReviewProblemItem_(number, item, '초안 작성 과정이 섞여 있어 수동 검수가 필요합니다.');
    }
    if (formMismatchSet[number]) {
      return buildReviewProblemItem_(number, item, '문항 형식이 생성 계획과 다릅니다.');
    }
    if (missingRequiredImageSet[number]) {
      return buildProblemWithFallbackImageTags_(number, item, existing);
    }
    if (imageSpecIssueSet[number]) {
      return {
        number,
        problem: '[검수 필요: 이미지 명세가 이미지생성기 형식과 맞지 않습니다.]\n' + stripImageTags_(String(existing.problem || existing.body || '').trim()),
        answer: String(existing.answer || '').trim() || '[검수 필요: 정답 누락]',
        solution: String(existing.solution || '').trim() || '[검수 필요: 해설 누락]',
        body: String(existing.body || '').trim(),
        needsReview: true
      };
    }
    if (incompleteSet[number]) {
      return {
        number,
        problem: String(existing.problem || existing.body || '').trim() || buildReviewProblemText_(item, '문제 본문 누락'),
        answer: String(existing.answer || '').trim() || '[검수 필요: 정답 누락]',
        solution: String(existing.solution || '').trim() || '[검수 필요: 해설 누락]',
        body: String(existing.body || '').trim(),
        needsReview: true
      };
    }
    return existing;
  });
}

function buildReviewProblemItem_(number, planItem, reason) {
  return {
    number,
    problem: buildReviewProblemText_(planItem, reason),
    answer: '[검수 필요]',
    solution: '[검수 필요: ' + reason + ']',
    body: '',
    needsReview: true
  };
}

function buildReviewProblemText_(planItem, reason) {
  return '[검수 필요: ' + reason + ' / 약점유형: ' + planItem.weakType + ', 생성유형: ' + planItem.formType + ', 난이도: ' + planItem.difficulty + ']';
}

function toNumberSet_(numbers) {
  const set = {};
  (numbers || []).forEach(number => set[Number(number)] = true);
  return set;
}

function hasGeneratedProblemReviewItems_(generatedProblems) {
  return (generatedProblems || []).some(item => item && item.needsReview);
}

function hasGeneratedProblemFormMismatch_(generatedItem, planItem) {
  if (!generatedItem || !planItem) return false;
  const formType = String(planItem.formType || '').trim();
  const problemText = String(generatedItem.problem || generatedItem.body || '').trim();
  if (!formType || !problemText) return false;

  const hasOptions = hasMultipleChoiceOptions_(problemText);
  if (formType === '5지선다형') return !hasOptions;
  if (formType === '단답형') return hasOptions || hasEssayInstruction_(problemText);
  if (formType === '서술형') return hasOptions || !hasEssayInstruction_(problemText);
  return false;
}

function hasMultipleChoiceOptions_(text) {
  const source = String(text || '');
  const circledCount = (source.match(/[①②③④⑤]/g) || []).length;
  if (circledCount >= 4) return true;
  return /(?:^|\n)\s*1[).]\s+[\s\S]*(?:^|\n)\s*5[).]\s+/m.test(source);
}

function hasEssayInstruction_(text) {
  return /(?:풀이\s*과정|자세히\s*서술|서술하시오|설명하시오|이유를\s*쓰|근거를\s*쓰|과정을\s*쓰)/.test(String(text || ''));
}

function hasBadImageSpec_(generatedItem, planItem) {
  if (!generatedItem) return false;
  const text = String(generatedItem.problem || generatedItem.body || '');
  const specs = extractImageSpecs_(text);
  if (!specs.korean.length && !specs.english.length) return false;
  if (hasImageAnswerLeakProblemText_(text, planItem)) return true;
  if (specs.korean.length !== specs.english.length) return true;
  return specs.korean.some(spec => !isStructuredImageSpec_(spec, 'ko')) ||
    specs.english.some(spec => !isStructuredImageSpec_(spec, 'en')) ||
    specs.english.some(spec => hasInvalidPointLabelSpec_(spec));
}

function hasImageAnswerLeakProblemText_(text, planItem) {
  const source = [
    text,
    planItem && planItem.weakType
  ].join('\n');
  return isImageAnswerLeakRiskType_(source);
}

function hasImageTags_(generatedItem) {
  if (!generatedItem) return false;
  const text = String(generatedItem.problem || generatedItem.body || '');
  const specs = extractImageSpecs_(text);
  return specs.korean.length > 0 && specs.english.length > 0;
}

function extractImageSpecs_(text) {
  return {
    korean: extractTaggedBlocks_(text, '이미지 필요'),
    english: extractTaggedBlocks_(text, 'IMAGE_PROMPT')
  };
}

function extractTaggedBlocks_(text, label) {
  const specs = [];
  const escapedLabel = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const regex = new RegExp('\\[' + escapedLabel + '\\d*\\s*:([\\s\\S]*?)\\]', 'g');
  let match;
  while ((match = regex.exec(String(text || ''))) !== null) {
    specs.push(String(match[1] || '').trim());
  }
  return specs;
}

function parseImageSpecKeyValues_(spec) {
  const result = {};
  String(spec || '')
    .replace(/\\n/g, '\n')
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
    .forEach(line => {
      const match = line.match(/^([^=]+)=(.*)$/);
      if (!match) return;
      result[String(match[1] || '').trim()] = String(match[2] || '').trim();
    });
  return result;
}

function hasInvalidPointLabelSpec_(spec) {
  const values = parseImageSpecKeyValues_(spec);
  const points = String(values.points || '').trim();
  const labels = String(values.labels || '').trim();
  const ambiguousPoints = !points || /문제\s*본문|제시된\s*점|주어진\s*점|given\s*points?|given\s*graph|as\s*shown/i.test(points);
  const hasConcretePoint = /[A-Za-z가-힣]?\s*\(\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\)/.test(points);
  if (points && (ambiguousPoints || !hasConcretePoint)) return true;
  if (labels && !hasConcretePoint) return true;
  return false;
}

function isStructuredImageSpec_(spec, language) {
  const lines = String(spec || '')
    .replace(/\\n/g, '\n')
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean);
  if (!lines.length) return false;

  const allowedKeys = {
    '종류': true,
    '식': true,
    'x범위': true,
    'y범위': true,
    '점': true,
    '교점': true,
    '꼭짓점': true,
    '축': true,
    '영역': true,
    '표시': true,
    '도형': true,
    '좌표': true,
    '변': true,
    '각': true,
    '직각': true,
    '평행': true,
    '수직': true,
    '원': true,
    '중심': true,
    '반지름': true
  };
  const allowedEnglishKeys = {
    'type': true,
    'template': true,
    'equation': true,
    'equations': true,
    'equation1': true,
    'equation2': true,
    'equation_left': true,
    'equation_right': true,
    'equation_top': true,
    'equation_bottom': true,
    'horizontal_y': true,
    'vertical_x': true,
    'curve_labels': true,
    'choices': true,
    'correct': true,
    'x_left': true,
    'x_right': true,
    'x_range': true,
    'y_range': true,
    'x_intercept': true,
    'show_vertex': true,
    'show_x_intercepts': true,
    'show_y_intercept': true,
    'points': true,
    'intersections': true,
    'vertex': true,
    'axis': true,
    'region': true,
    'labels': true,
    'shape': true,
    'coordinates': true,
    'segments': true,
    'angles': true,
    'right_angle': true,
    'parallel': true,
    'perpendicular': true,
    'circle': true,
    'center': true,
    'radius': true
    ,'width': true
    ,'height': true
    ,'road_width': true
    ,'road_count': true
    ,'inner_width': true
    ,'inner_height': true
    ,'border_width': true
    ,'total_length': true
    ,'left_side': true
    ,'right_side': true
    ,'paper_width': true
    ,'paper_height': true
    ,'paper_side': true
    ,'cut_side': true
    ,'shade': true
    ,'unit': true
  };
  const allowed = language === 'en' ? allowedEnglishKeys : allowedKeys;
  const ambiguousText = /(?:아래로\s*볼록|위로\s*볼록|그림과\s*같이|아래\s*그림|위\s*그림|주어진\s*그래프|문제\s*본문|색칠|대략|적당히|그래프\s*=|roughly|approximately|as shown|given graph|shade|shaded|nice|pretty)/i;
  const forbiddenVisibleEnglishLabels = /\blabels\s*=.*\b(?:root|vertex|point|axes|axis|graph|parabola|given|region|segment|parallel|relationship|intercept)\b/i;

  return lines.every(line => {
    const match = line.match(/^([^=]+)=.+$/);
    if (!match) return false;
    const key = String(match[1] || '').trim();
    if (!allowed[key]) return false;
    if (language === 'en' && forbiddenVisibleEnglishLabels.test(line)) return false;
    if (language === 'en' && /[가-힣]/.test(line)) return false;
    if (hasUnresolvedImageSpecValue_(key, line)) return false;
    return !ambiguousText.test(line);
  });
}

function hasUnresolvedImageSpecValue_(key, line) {
  const value = String(line || '').split('=').slice(1).join('=').trim();
  if (/문제\s*본문|제시된\s*점|주어진\s*점|given\s*points?|given\s*graph|as\s*shown/i.test(value)) return true;
  if (key === 'labels' && hasFormulaLikeLabel_(value)) return true;
  if (key === '식' || key === 'equation' || key === 'equations' || key === 'choices' || key === 'equation1' || key === 'equation2' || key === 'equation_left' || key === 'equation_right' || key === 'equation_top' || key === 'equation_bottom' || key === 'labels') {
    return hasUnresolvedEquationLetters_(value);
  }
  return false;
}

function hasFormulaLikeLabel_(value) {
  return /(?:=|\^|²|√|[xy]\b|\b[xy]\s*[+-]|\d+\s*[xy]|\b[xy]\s*\()/i.test(String(value || ''));
}

function hasUnresolvedEquationLetters_(value) {
  const source = String(value || '')
    .replace(/x_range|y_range|x_left|x_right|type|template|coordinate_plane|geometry|parabola_band_area|multiple_choice_parabola_position|equation_top|equation_bottom|choices|correct/gi, '')
    .replace(/[A-Z]\s*(?=\(|,|$)/g, '')
    .replace(/\b(?:sin|cos|tan|log|ln)\b/gi, '');
  if (/(^|[^A-Za-z])(?:a|b|c|d|e|f|g|h|i|j|k|l|m|n|p|q|r|s|t|u|v|w|z)\s*(?:\*?\s*x|\()/i.test(source)) return true;
  return /(?:α|β|γ|theta|alpha|beta|gamma|(^|[^A-Za-z])(?:a|b|c|d|e|f|g|h|i|j|k|l|m|n|p|q|r|s|t|u|v|w|z)(?=[^A-Za-z]|$))/i.test(source);
}

function buildSimilarProblemsPrompt_(studentName, examName, wrongProblems, reportText, rulesByType, planItems) {
  const curriculumLines = buildCurriculumPromptLines_(planItems);
  return [
    '너는 20년 경력의 베테랑 중고등학교 수학문제 출제자다.',
    '학생명: ' + studentName,
    '시험명: ' + examName,
    '분석보고서 참고자료: ' + (reportText || '분석보고서 파일이 없으므로 오답 유형만 참고한다.'),
    '오답 목록(JSON): ' + JSON.stringify(wrongProblems),
    '유형별 생성 규칙(JSON): ' + JSON.stringify(rulesByType),
    '이번 호출에서 생성할 문항 계획(JSON): ' + JSON.stringify(planItems),
    '',
    '요구사항:'
  ].concat(curriculumLines).concat([
    '- 반드시 문항 계획의 약점유형, 생성유형, 문항번호, 난이도를 그대로 따른다.',
    '- 생성유형이 5지선다형이면 문제 본문에 반드시 ①, ②, ③, ④, ⑤ 선택지를 모두 포함하라.',
    '- 생성유형이 단답형이면 ①, ②, ③, ④, ⑤ 선택지를 절대 쓰지 말고, 최종 답만 요구하는 문항으로 작성하라.',
    '- 생성유형이 서술형이면 ①, ②, ③, ④, ⑤ 선택지를 절대 쓰지 말고, "풀이 과정을 자세히 서술하시오." 또는 그에 준하는 서술 요구 문장을 포함하라.',
    '- number 값은 반드시 문항 계획의 number를 그대로 사용하라. 각 호출 안에서 1, 2, 3으로 다시 번호를 매기지 말라.',
    '- 수식은 x^2가 아니라 유니코드 지수 형태로 작성한다.',
    '- 문제에 등장하는 수식은 반드시 [수식: ...] 형태로 작성한다.',
    '- LaTeX, $...$, \\( ... \\), \\frac, \\sqrt 표기는 쓰지 말고 x², √3, 3/4처럼 일반 텍스트와 유니코드 기호로 적어라.',
    '- \\Rightarrow, \\pm, \\times 같은 LaTeX 명령도 쓰지 말고 ⇒, ±, × 같은 유니코드 기호를 써라.',
    '- 예: [수식: x² - 5x + 6 = 0], [수식: t = -b / 2a], [수식: √3 / 2]',
    '- 문항 계획(JSON)에 imageRequired가 true인 문항은 반드시 [이미지 필요번호: ...]와 [IMAGE_PROMPT번호: ...] 두 이미지 태그를 모두 포함하라.',
    '- imageRequired가 true인데 이미지 태그를 빠뜨린 문항은 불합격이다. 해당 문항은 문제 본문 시작 전에 반드시 두 이미지 태그부터 작성하라.',
    '- imageRequired가 true인 문항은 imageKind가 coordinate_plane이면 좌표평면/그래프가 필요한 문제로, geometry이면 도형 그림이 필요한 문제로 작성하라.',
    '- 이미지가 문제의 정답을 바로 드러내면 안 된다. "지나지 않는 사분면", "그래프가 지나는 사분면", "위치 판단", "해의 개수", "최댓값/최솟값"처럼 완성된 그래프 자체가 답을 노출하는 문항에는 이미지 태그를 쓰지 말라.',
    '- 이미지가 필요한 문항은 그림이 주어진 조건 자료가 되도록 만들고, 그림을 보고 바로 답만 읽는 문제가 아니라 계산이나 추론이 남아 있게 작성하라.',
    '- imageRequired가 false인 문항도 그림이 자연스러우면 이미지 태그를 사용해도 된다. 단, 이미지 태그를 쓰면 반드시 한글/영어 두 태그를 함께 써라.',
    '- 이미지가 필요하면 문항번호 바로 뒤에 [이미지 필요번호: ...]와 [IMAGE_PROMPT번호: ...] 두 태그를 반드시 함께 적고, 그 다음 줄부터 문제 본문을 시작하라.',
    '- 이미지 태그 번호는 문항번호와 반드시 같게 하라. 예: 문항7의 그림은 [이미지 필요7: ...]와 [IMAGE_PROMPT7: ...]로 작성한다.',
    '- 이미지생성기는 [IMAGE_PROMPT번호: ...]의 번호를 파일명 번호로 사용한다. 예: [IMAGE_PROMPT7: ...]는 원본파일명_이미지7.PNG로 저장된다.',
    '- [이미지 필요번호: ...]는 사람이 검수할 수 있는 한글 key=value 명세로 작성하라.',
    '- [IMAGE_PROMPT번호: ...]는 이미지생성기가 읽을 수 있는 영어 key=value 명세로 작성하라.',
    '- 두 이미지 태그에는 같은 수식, 좌표, 범위, 점 이름이 들어가야 한다.',
    '- 이미지 태그의 식, equation에는 a, b, c, f, g, m, n, p, q, k, α, β, alpha, beta 같은 미정 계수를 남기지 말고 반드시 계산이 끝난 실제 함수식만 적어라.',
    '- equation에는 y = k(x-alpha)(x-beta), y = k(x-α)(x-β)처럼 기호가 남은 인수분해형을 절대 쓰지 말라. 근과 계수를 계산해 y = -x^2 + 2x + 3처럼 실제 그래프식으로 작성하라.',
    '- equation에는 y = f(x), y = g(x)를 절대 쓰지 말라. 문제 본문에서 f(x), g(x)의 실제 식을 계산해 y = 1/2*x^2 + 3처럼 숫자 계수만 있는 식으로 작성하라.',
    '- 방정식 -x^2 + 2x + 3 = 0의 해를 묻는 문제라도 IMAGE_PROMPT의 equation에는 y = -x^2 + 2x + 3처럼 그래프식으로 적어라.',
    '- [IMAGE_PROMPT번호: ...] 내부에는 한글을 쓰지 말라. points 값에도 "문제 본문에 제시된 점" 같은 설명 대신 A(1,2), B(3,4)처럼 실제 좌표를 적어라.',
    '- 실제 좌표를 계산할 수 없는 점은 points에 쓰지 말라. "문제 본문에 제시된 점", "given points" 같은 참조 문구는 절대 금지다.',
    '- points에 실제 좌표가 없으면 labels도 절대 쓰지 말라. labels=A, B, C를 쓰려면 points에 A(1,2), B(3,4), C(0,0)처럼 같은 점의 실제 좌표가 반드시 있어야 한다.',
    '- [IMAGE_PROMPT번호: ...]의 labels 값에는 root, vertex, point, axes, graph, parabola, given, region 같은 영어 표시 문구를 쓰지 말라. 그림에 보이는 라벨은 A, B, C 같은 점 이름만 허용하고 함수식은 절대 labels에 넣지 말라.',
    '- 두 함수 또는 두 이차함수와 두 수직선 x=a, x=b로 둘러싸인 넓이 문제는 자유 coordinate_plane 명세를 쓰지 말고 반드시 template=parabola_band_area를 사용하라.',
    '- y = (1/2)x²와 y = g(x), x = 0, x = 2처럼 한 함수가 f(x) 또는 g(x)로 불리더라도 실제 식을 계산한 뒤 template=parabola_band_area를 사용하라.',
    '- 나쁜 IMAGE_PROMPT 예: equation=y = (1/2)x^2, y = g(x), x = 0, x = 2 / points=문제 본문에 제시된 점. 이런 명세는 렌더링 실패로 간주된다.',
    '- 좋은 IMAGE_PROMPT 예: template=parabola_band_area / equation_top=y = (1/2)*x^2 + 3 / equation_bottom=y = (1/2)*x^2 / x_left=0 / x_right=2. 단, equation_top/equation_bottom에는 문제에서 계산된 실제 식을 넣어야 한다.',
    '- template=parabola_band_area 필수 영어 항목은 template, equation_top, equation_bottom, x_left, x_right뿐이다. equation_top/equation_bottom에는 실제 y=... 식을 쓰고, x_left/x_right에는 숫자만 쓴다.',
    '- template=parabola_band_area에서는 type, equation, x_range, y_range, region, labels를 쓰지 말라. 렌더러가 자동으로 축 범위, 색칠, x값 라벨을 처리한다.',
    '- 이차함수의 x축 교점, y축 교점, 꼭짓점, 삼각형 넓이 그림은 반드시 전용 템플릿을 사용하라. 허용 템플릿: parabola_basic_shape, parabola_xintercepts_vertex_triangle, parabola_xintercepts_yintercept_triangle, parabola_yintercept_vertex_xintercept_triangle.',
    '- "x축과 만나는 두 점을 A, B라 하고 꼭짓점을 C라고 할 때 삼각형 ABC의 넓이" 유형은 반드시 template=parabola_xintercepts_vertex_triangle을 사용하라. type=coordinate_plane, labels=A,B,C, points 없음 형태로 쓰면 실패로 간주된다.',
    '- parabola_basic_shape는 equation만 쓰면 렌더러가 꼭짓점, x축 교점, y축 교점을 자동 계산해 표시한다. 필요하면 show_vertex=false, show_x_intercepts=false, show_y_intercept=false를 쓸 수 있다.',
    '- parabola_xintercepts_vertex_triangle은 equation만 쓰면 렌더러가 두 x축 교점 A,B와 꼭짓점 C를 계산하고 삼각형 ABC를 색칠한다.',
    '- parabola_xintercepts_yintercept_triangle은 equation만 쓰면 렌더러가 두 x축 교점 A,B와 y축 교점 C를 계산하고 삼각형 ABC를 색칠한다.',
    '- parabola_yintercept_vertex_xintercept_triangle은 equation과 x_intercept=positive 또는 negative를 쓰면 렌더러가 y축 교점 A, 꼭짓점 B, 선택한 x축 교점 C를 계산하고 삼각형 ABC를 색칠한다.',
    '- 원점을 지나는 두 이차함수와 직선 y=k가 만나는 그림은 template=two_origin_parabolas_horizontal_line을 사용하라. 필수 항목은 equation1, equation2, horizontal_y이다.',
    '- 원점을 지나는 두 이차함수와 직선 x=a가 만나는 그림은 template=two_origin_parabolas_vertical_line_ratio를 사용하라. 필수 항목은 equation1, equation2, vertical_x이다.',
    '- 두 이차함수 사이에 둘러싸인 렌즈형/잎사귀형 색칠 영역은 template=two_parabolas_between_area를 사용하라. 필수 항목은 equation1, equation2이다.',
    '- 원점을 지나는 여러 이차함수 그래프를 비교하는 보기형 그림은 template=parabola_family_origin을 사용하라. 필수 항목은 equations이고, 필요하면 curve_labels=a,b,c처럼 쓴다.',
    '- 이차방정식의 두 근, 아래로/위로 볼록, 꼭짓점 위치, x절편 조건을 보고 ①~⑤ 그래프를 고르는 문제는 한 장짜리 coordinate_plane을 쓰지 말고 반드시 template=multiple_choice_parabola_position을 사용하라.',
    '- template=multiple_choice_parabola_position 필수 항목은 choices이다. choices에는 ①~⑤에 해당하는 실제 y=... 식 5개를 세미콜론(;)으로 구분해 적어라. choices에 y=f(x), y=g(x), k(x-alpha)(x-beta), 미정계수, 설명문을 넣지 말라.',
    '- 이차함수 전용 템플릿에서는 points, labels, region을 쓰지 말라. 점 좌표와 색칠 영역은 렌더러가 equation에서 계산한다.',
    '- 이차방정식 활용 도형 문제는 가능한 한 도형 전용 템플릿을 사용하라. 허용 템플릿: rectangle_cross_road, rectangle_slanted_cross_road, rectangle_multi_slanted_roads, rectangular_park_border, two_squares_on_segment, open_box_net_equal_cuts, open_box_net_rectangular_paper.',
    '- 도로 문제는 rectangle_cross_road 또는 rectangle_slanted_cross_road 또는 rectangle_multi_slanted_roads를 사용하라. 필수 항목은 width, height, road_width이다. road_width는 문제에서 구할 값이면 x를 써도 된다.',
    '- 공원 둘레 산책로 문제는 rectangular_park_border를 사용하라. 필수 항목은 inner_width, inner_height, border_width이다. inner_width나 inner_height는 문제에서 구할 값이면 x, x+12 같은 표현을 쓸 수 있다.',
    '- 선분을 둘로 나누어 두 정사각형을 만드는 문제는 two_squares_on_segment를 사용하라. 필수 항목은 total_length이다.',
    '- 정사각형 종이 네 귀퉁이를 잘라 상자를 만드는 문제는 open_box_net_equal_cuts를 사용하라. 필수 항목은 paper_side, cut_side이다. 직사각형 종이는 open_box_net_rectangular_paper를 사용하고 paper_width, paper_height, cut_side를 쓴다.',
    '- 도형 전용 템플릿에서는 미지수 길이 x를 허용한다. 단, 함수 그래프 equation에는 여전히 미정계수나 g(x)를 남기지 말라.',
    '- 넓이 문제처럼 색칠 영역이 필요하면 region 값에 실제 경계를 적어라. 예: region=between y=x^2 and y=(x-4)^2 for 1<=y<=9 또는 region=between y=2x^2 and y=2x^2+5 for -2<=x<=1.',
    '- 색칠이 필요 없는 그림이면 region을 쓰지 말라. region 없이 이미지생성기/렌더러가 임의로 영역을 색칠한다고 기대하지 말라.',
    '- 한글 그래프 명세는 종류=좌표평면, 식=, x범위=, y범위=, 점=, 교점=, 꼭짓점=, 축=, 영역=, 표시= 항목만 사용하라.',
    '- 영어 그래프 명세는 type=coordinate_plane, equation=, x_range=, y_range=, points=, intersections=, vertex=, axis=, region=, labels= 또는 template=parabola_band_area, equation_top=, equation_bottom=, x_left=, x_right= 또는 이차함수 전용 template, equation=, equations=, choices=, correct=, equation1=, equation2=, equation_left=, equation_right=, horizontal_y=, vertical_x=, curve_labels=, x_intercept=, show_vertex=, show_x_intercepts=, show_y_intercept= 항목만 사용하라.',
    '- 한글 도형 명세는 종류=도형, 도형=, 점=, 좌표=, 변=, 각=, 직각=, 평행=, 수직=, 원=, 중심=, 반지름=, 표시= 항목만 사용하라.',
    '- 영어 도형 명세는 type=geometry, shape=, points=, coordinates=, segments=, angles=, right_angle=, parallel=, perpendicular=, circle=, center=, radius=, labels= 또는 도형 전용 template, width=, height=, road_width=, road_count=, inner_width=, inner_height=, border_width=, total_length=, paper_width=, paper_height=, paper_side=, cut_side=, shade=, unit= 항목만 사용하라.',
    '- 이미지 명세에는 "문제 본문 참고", "주어진 그래프", "위 그림", "아래로 볼록한 포물선", "색칠하여 표시", "그림과 같이", "roughly", "as shown", "shaded" 같은 모호한 문장을 쓰지 말고 실제 식, 좌표, 범위, 점 이름, 선분, 각도, 길이를 명시하라.',
    '- 예: [이미지 필요7:\\n종류=좌표평면\\n식=y = x² - 4x + 3\\nx범위=-1..5\\ny범위=-2..8\\n점=A(1,0), B(3,0), C(2,-1)\\n표시=점 A, 점 B, 점 C] [IMAGE_PROMPT7:\\ntype=coordinate_plane\\nequation=y = x^2 - 4x + 3\\nx_range=-1..5\\ny_range=-2..8\\npoints=A(1,0), B(3,0), C(2,-1)\\nlabels=A, B, C]',
    '- 예: [이미지 필요8:\\n종류=좌표평면\\n식=y = x² + 2, y = x² - 3\\n영역=두 그래프와 x=1, x=4 사이\\n표시=x=1, x=4, 색칠 영역] [IMAGE_PROMPT8:\\ntemplate=parabola_band_area\\nequation_top=y = x^2 + 2\\nequation_bottom=y = x^2 - 3\\nx_left=1\\nx_right=4]',
    '- 예: [이미지 필요9:\\n종류=좌표평면\\n식=y = x² - 16\\n표시=x축 교점 A,B, 꼭짓점 C, 삼각형 ABC] [IMAGE_PROMPT9:\\ntemplate=parabola_xintercepts_vertex_triangle\\nequation=y = x^2 - 16]',
    '- 예: [이미지 필요10:\\n종류=좌표평면\\n식=y = -x² + 4x + 5\\n표시=x축 교점 A,B, y축 교점 C, 삼각형 ABC] [IMAGE_PROMPT10:\\ntemplate=parabola_xintercepts_yintercept_triangle\\nequation=y = -x^2 + 4*x + 5]',
    '- 예: [이미지 필요11:\\n종류=좌표평면\\n식=y = 1/2*x² - 2x - 6\\n표시=y축 교점 A, 꼭짓점 B, x축 양의 교점 C, 삼각형 ABC] [IMAGE_PROMPT11:\\ntemplate=parabola_yintercept_vertex_xintercept_triangle\\nequation=y = 1/2*x^2 - 2*x - 6\\nx_intercept=positive]',
    '- 예: [이미지 필요13:\\n종류=좌표평면\\n식=y = x², y = 1/4*x², y = 4\\n표시=P, Q, R] [IMAGE_PROMPT13:\\ntemplate=two_origin_parabolas_horizontal_line\\nequation1=y = x^2\\nequation2=y = 1/4*x^2\\nhorizontal_y=4]',
    '- 예: [이미지 필요14:\\n종류=좌표평면\\n식=y = 1/3*x², y = 2*x², x = 1\\n표시=A, B, C] [IMAGE_PROMPT14:\\ntemplate=two_origin_parabolas_vertical_line_ratio\\nequation1=y = 1/3*x^2\\nequation2=y = 2*x^2\\nvertical_x=1]',
    '- 예: [이미지 필요15:\\n종류=좌표평면\\n식=y = x², y = -x² + 4\\n영역=두 그래프 사이] [IMAGE_PROMPT15:\\ntemplate=two_parabolas_between_area\\nequation1=y = x^2\\nequation2=y = -x^2 + 4]',
    '- 예: [이미지 필요16:\\n종류=좌표평면\\n식=y = 2/5*x², y = x², y = -x²\\n표시=a,b,c] [IMAGE_PROMPT16:\\ntemplate=parabola_family_origin\\nequations=y = 2/5*x^2, y = x^2, y = -x^2\\ncurve_labels=a,b,c]',
    '- 예: [이미지 필요21:\\n종류=보기 그래프\\n표시=①~⑤ 이차함수 그래프] [IMAGE_PROMPT21:\\ntemplate=multiple_choice_parabola_position\\nchoices=y = (x - 1)*(x - 3); y = -(x - 1)*(x - 3); y = (x + 1)*(x - 3); y = (x - 2)^2 + 1; y = (x + 1)*(x + 3)]',
    '- 예: [이미지 필요17:\\n종류=도형\\n도형=직사각형 밭과 십자 도로\\n가로=40\\n세로=30\\n도로폭=x] [IMAGE_PROMPT17:\\ntemplate=rectangle_cross_road\\nwidth=40\\nheight=30\\nroad_width=x]',
    '- 예: [이미지 필요18:\\n종류=도형\\n도형=직사각형 공원과 산책로\\n공원가로=x+12\\n공원세로=x\\n산책로폭=6] [IMAGE_PROMPT18:\\ntemplate=rectangular_park_border\\ninner_width=x+12\\ninner_height=x\\nborder_width=6]',
    '- 예: [이미지 필요19:\\n종류=도형\\n도형=선분 위 두 정사각형\\n전체길이=11] [IMAGE_PROMPT19:\\ntemplate=two_squares_on_segment\\ntotal_length=11]',
    '- 예: [이미지 필요20:\\n종류=도형\\n도형=정사각형 종이에서 네 귀퉁이 자르기\\n한변=10\\n자른정사각형=x] [IMAGE_PROMPT20:\\ntemplate=open_box_net_equal_cuts\\npaper_side=10\\ncut_side=x]',
    '- 예: [이미지 필요12:\\n종류=도형\\n도형=직각삼각형\\n점=A,B,C\\n좌표=A(0,0), B(4,0), C(4,3)\\n직각=B\\n변=AB=4, BC=3, AC=5] [IMAGE_PROMPT12:\\ntype=geometry\\nshape=right_triangle\\npoints=A,B,C\\ncoordinates=A(0,0), B(4,0), C(4,3)\\nright_angle=B\\nsegments=AB=4, BC=3, AC=5\\nlabels=A, B, C]',
    '- 원문 시험 문제를 그대로 복제하지 말고, 선정된 약점유형과 틀린 문항만 참고한다.',
    '- 쌍둥이_규칙의 생성규칙과 금지사항을 반드시 지킨다.',
    '- JSON을 사용하지 말고 아래 구분자 형식만 반복해서 반환하라. 마크다운, 코드블록, 전체 설명은 금지.',
    '- 영어로 된 Concept, Scenario, Difficulty, Details 같은 출제 계획/메타 설명을 절대 쓰지 말라.',
    '- 문제, 정답, 해설은 모두 한국어로 작성하라. 단, [IMAGE_PROMPT번호: ...] 태그 내부의 key=value 명세만 영어를 허용한다.',
    '- 생각 과정, 출제 의도, 분석 메모, bullet point, 제목, 요약을 쓰지 말고 최종 문항만 한국어로 작성하라.',
    '- "잠시만", "다시 시도", "문제 설정을 변경", "계산이 깔끔하게", "역설계", "내가 만든 문제", "다시 조정합니다", "혼동했습니다"처럼 초안 작성 과정이나 자기 수정 흔적을 절대 쓰지 말라.',
    '- Wait, I might, Let me, re-evaluate, confusing, original problem 같은 영어 자기검토 문장을 절대 쓰지 말라.',
    '- 정답: 영역에는 최종 정답만 적고, 정답을 정하기 위한 고민이나 대안 설명을 쓰지 말라.',
    '- 해설: 영역에는 최종 확정된 문제에 대한 풀이만 적어라. 문제를 만들다가 수정한 과정, 실패한 계산, 대안 문제, 문제와 정답의 조화를 맞추는 과정은 절대 쓰지 말라.',
    '- 정답과 해설에는 "정답을 다시 확인", "오답 목록", "제가", "저의", "만약", "현재 문제", "다시 계산", "올바른 정답을 도출" 같은 자기검토 표현을 절대 쓰지 말라.',
    '- 정답과 해설에는 백틱(`), 괄호로 감싼 메타 설명, 원본 오답 목록이나 생성 규칙을 언급하는 문장을 절대 쓰지 말라.',
    '- 첫 글자는 반드시 ===문항_START=== 여야 하며, 그 앞에 어떤 문장도 쓰지 말라.',
    '- 각 문항은 반드시 ===문항_START=== 로 시작하고 ===문항_END=== 로 끝낸다.',
    '- 번호 값은 반드시 문항 계획의 number를 그대로 사용하라.',
    '- 문제: 영역에는 문제 본문만 쓰고 정답/해설을 포함하지 말라.',
    '- 정답: 영역과 해설: 영역은 반드시 각각 비우지 말고 작성하라.',
    '- 정답과 해설을 문제 본문 아래에 섞어 쓰지 말고, 반드시 정답: 라벨과 해설: 라벨 뒤에 분리해서 작성하라.',
    '- 형식:',
    '===문항_START===',
    '번호: 1',
    '문제:',
    '문항1. ...',
    '정답:',
    '정답 내용',
    '해설:',
    '해설 내용',
    '===문항_END==='
  ]).join('\n');
}

function buildCurriculumPromptLines_(planItems) {
  const level = getPlanItemsCurriculumLevel_(planItems);
  if (level === 'MIDDLE') {
    return [
      '- 모든 문제와 해설은 중학교 수학 교육과정 안에서만 작성하라.',
      '- 정적분, 적분, 미분, 도함수, 극한, ∫, dx, lim 같은 고등학교/대학 과정 개념과 표기를 절대 쓰지 말라.',
      '- 그래프 사이 넓이 문제는 정적분으로 풀어야 하는 형태로 만들지 말라. 반드시 직사각형, 삼각형, 사다리꼴, 평행선 사이 거리, 좌표평면의 기본 넓이 공식으로 풀리게 만들어라.',
      '- 두 곡선 사이 넓이를 만들 때는 두 그래프의 세로 차이가 일정하거나, x축/y축에 평행한 선분과 간단한 도형 넓이로 해결되는 조건만 사용하라.'
    ];
  }
  if (level === 'HIGH') {
    return [
      '- 문제은행의 단원, 유형, 난이도에 맞는 고등학교 수학 교육과정 안에서 문제와 해설을 작성하라.',
      '- 고등학교 과정의 개념은 해당 약점유형을 해결하는 데 필요한 경우에만 사용하라.'
    ];
  }
  return [
    '- 문제은행의 상위 단원, 하위 단원, 문제 유형 수준을 벗어나지 말라.',
    '- 적분, 미분, 극한 유형이 아닌 문항에는 정적분, 미분, 도함수, 극한 같은 상위 도구를 새로 도입하지 말라.'
  ];
}

function getPlanItemsCurriculumLevel_(planItems) {
  const levels = unique_((planItems || []).map(item => String(item.curriculumLevel || '')).filter(Boolean));
  if (levels.indexOf('HIGH') >= 0) return 'HIGH';
  if (levels.indexOf('MIDDLE') >= 0) return 'MIDDLE';
  return 'UNKNOWN';
}

function buildTwinGenerationPlan_(wrongProblems, examName, targetSheetName) {
  const curriculumLevel = inferCurriculumLevel_(examName, wrongProblems);
  const weakTypes = unique_(wrongProblems.map(item => item.type));
  const countConfig = getSimilarProblemCountConfig_(targetSheetName, weakTypes.length);
  const totalCount = countConfig.totalCount;
  const countByType = distributeCounts_(totalCount, weakTypes);
  const formOrder = ['5지선다형', '단답형', '서술형'];
  const itemsWithoutNumber = [];

  weakTypes.forEach(type => {
    const formCounts = distributeCounts_(countByType[type], formOrder);
    formOrder.forEach(form => {
      for (let i = 0; i < formCounts[form]; i++) {
        itemsWithoutNumber.push({ weakType: type, formType: form });
      }
    });
  });

  const numbered = [];
  formOrder.forEach(form => {
    const formItems = shuffle_(itemsWithoutNumber.filter(item => item.formType === form));
    formItems.forEach((item, index) => {
      numbered.push({
        number: numbered.length + 1,
        formOrdinal: index + 1,
        weakType: item.weakType,
        formType: item.formType,
        difficulty: (index + 1) % 2 === 1 ? '중' : randomChoice_(['상', '하'])
      });
    });
  });

  const items = applyCurriculumLevelToPlan_(applyImageRequirementsToPlan_(numbered, countConfig.imageProblemCount), curriculumLevel);

  return {
    totalCount,
    weakTypes,
    countByType,
    generalProblemCount: countConfig.generalProblemCount,
    requestedImageProblemCount: countConfig.imageProblemCount,
    curriculumLevel,
    imageRequiredCount: items.filter(item => item.imageRequired).length,
    items
  };
}

function getSimilarProblemCountConfig_(targetSheetName, weakTypeCount) {
  const defaultTotal = getTwinProblemTotalCount_(weakTypeCount);
  const matchingConfigs = readAdminConfigs_()
    .filter(config => config.feature === TASK_TYPES.SIMILAR_PROBLEMS)
    .filter(config => config.enabled)
    .filter(config => matchesSheetScope_(config.sheetScope, targetSheetName));
  const configured = matchingConfigs.find(config => config.generalProblemCount > 0 || config.imageProblemCount > 0);

  if (!configured) {
    return {
      totalCount: defaultTotal,
      generalProblemCount: defaultTotal - getRequiredImageProblemCount_(defaultTotal),
      imageProblemCount: getRequiredImageProblemCount_(defaultTotal)
    };
  }

  let imageCount = clampInteger_(configured.imageProblemCount, 0, 30);
  let generalCount = clampInteger_(configured.generalProblemCount, 0, 30);
  if (imageCount + generalCount <= 0) {
    generalCount = defaultTotal - getRequiredImageProblemCount_(defaultTotal);
    imageCount = getRequiredImageProblemCount_(defaultTotal);
  }
  if (imageCount + generalCount > 30) {
    generalCount = Math.max(0, 30 - imageCount);
    if (imageCount > 30) imageCount = 30;
  }

  return {
    totalCount: Math.max(1, imageCount + generalCount),
    generalProblemCount: generalCount,
    imageProblemCount: imageCount
  };
}

function inferCurriculumLevel_(examName, wrongProblems) {
  const source = [
    examName
  ].concat((wrongProblems || []).map(item => [
    item.problemNumber,
    item.rawType,
    item.type,
    item.unit1,
    item.unit2
  ].join(' '))).join(' ');

  if (/(?:고[123]|고등|수학\s*(?:Ⅰ|Ⅱ|I|II|1|2)|미적분|확률과\s*통계|기하)/.test(source)) return 'HIGH';
  if (/(?:중[123]|중학교|중등)/.test(source)) return 'MIDDLE';
  return 'UNKNOWN';
}

function applyCurriculumLevelToPlan_(items, curriculumLevel) {
  return (items || []).map(item => Object.assign({}, item, {
    curriculumLevel: curriculumLevel || 'UNKNOWN'
  }));
}

function buildReviewProblemWithExisting_(number, planItem, existing, reason) {
  const problem = stripImageTags_(String(existing && (existing.problem || existing.body) || '').trim());
  if (!problem) return buildReviewProblemItem_(number, planItem, reason);

  return {
    number,
    problem: '[검수 필요: ' + reason + ']\n' + problem,
    answer: String(existing.answer || '').trim() || '[검수 필요: 정답 누락]',
    solution: String(existing.solution || '').trim() || '[검수 필요: 해설 누락]',
    body: String(existing.body || '').trim(),
    needsReview: true
  };
}

function buildProblemWithFallbackImageTags_(number, planItem, existing) {
  const problem = String(existing && (existing.problem || existing.body) || '').trim();
  if (!problem) return buildReviewProblemItem_(number, planItem, '이미지 필수 문항인데 이미지 태그가 없습니다.');
  if (!canBuildReliableFallbackImageTags_(problem, planItem)) {
    return buildReviewProblemWithExisting_(number, planItem, existing, '이미지 필수 문항인데 이미지 명세가 부족합니다.');
  }

  return {
    number,
    problem: addFallbackImageTags_(problem, planItem, number),
    answer: String(existing.answer || '').trim() || '[검수 필요: 정답 누락]',
    solution: String(existing.solution || '').trim() || '[검수 필요: 해설 누락]',
    body: String(existing.body || '').trim(),
    needsReview: false
  };
}

function addFallbackImageTags_(problem, planItem, number) {
  if (hasImageTags_({ problem })) return problem;
  return buildFallbackImageTags_(problem, planItem, number) + '\n' + problem;
}

function buildFallbackImageTags_(problem, planItem, number) {
  const kind = String((planItem && planItem.imageKind) || getImageKindForWeakType_(planItem && planItem.weakType));
  return kind === 'geometry'
    ? buildFallbackGeometryTags_(problem, number)
    : buildFallbackCoordinatePlaneTags_(problem, number);
}

function canBuildReliableFallbackImageTags_(problem, planItem) {
  const kind = String((planItem && planItem.imageKind) || getImageKindForWeakType_(planItem && planItem.weakType));
  if (kind === 'geometry') {
    return extractNamedPoints_(problem).length >= 3;
  }
  if (buildTemplateFallbackCoordinatePlaneTags_(problem, 0)) return true;
  const yEquations = extractConcreteYEquationsForImage_(problem);
  const points = extractCoordinatePoints_(problem);
  const needsRelationshipDiagram = /(?:넓이|영역|둘러싸인|사다리꼴|삼각형|사각형|정사각형|내접|색칠)/.test(problem);
  return !needsRelationshipDiagram && (yEquations.length > 0 || points.length >= 2);
}

function buildFallbackCoordinatePlaneTags_(problem, number) {
  const templateTags = buildTemplateFallbackCoordinatePlaneTags_(problem, number);
  if (templateTags) return templateTags;

  const equations = extractConcreteYEquationsForImage_(problem).slice(0, 4);
  const points = extractCoordinatePoints_(problem).slice(0, 8);
  const equationText = equations.join(', ');
  const pointsText = points.join(', ');
  const englishEquationText = equationText.replace(/²/g, '^2');
  const koreanLines = [
    '[이미지 필요' + number + ':',
    '종류=좌표평면',
    '식=' + equationText,
    'x범위=-6..6',
    'y범위=-10..10'
  ];
  const englishLines = [
    '[IMAGE_PROMPT' + number + ':',
    'type=coordinate_plane',
    'equation=' + englishEquationText,
    'x_range=-6..6',
    'y_range=-10..10'
  ];
  if (points.length) {
    koreanLines.push('점=' + pointsText, '표시=점 이름');
    englishLines.push('points=' + pointsText);
  }
  return koreanLines.concat([
    ']',
  ], englishLines, [
    ']'
  ]).join('\n');
}

function buildTemplateFallbackCoordinatePlaneTags_(problem, number) {
  const yEquations = extractConcreteYEquationsForImage_(problem);
  const verticals = extractConcreteAxisLinesForImage_(problem, 'x');
  if (/넓이|둘러싸인|부분/.test(problem) && yEquations.length >= 2 && verticals.length >= 2) {
    return buildImageTagsFromLines_(number, [
      '종류=좌표평면',
      '식=' + yEquations.slice(0, 2).join(', '),
      '영역=두 그래프와 x=' + verticals[0] + ', x=' + verticals[1] + ' 사이',
      '표시=색칠 영역'
    ], [
      'template=parabola_band_area',
      'equation_top=' + toImageEquation_(yEquations[0]),
      'equation_bottom=' + toImageEquation_(yEquations[1]),
      'x_left=' + verticals[0],
      'x_right=' + verticals[1]
    ]);
  }

  if (/폭|좁|넓|그래프/.test(problem) && yEquations.length >= 3) {
    return buildImageTagsFromLines_(number, [
      '종류=좌표평면',
      '식=' + yEquations.slice(0, 4).join(', '),
      '표시=그래프 비교'
    ], [
      'template=' + (yEquations.length >= 4 ? 'parabola_four_family_origin' : 'parabola_family_origin'),
      'equations=' + yEquations.slice(0, 4).map(toImageEquation_).join(', ')
    ]);
  }

  if (/정사각형/.test(problem) && yEquations.length >= 1 && /그래프/.test(problem)) {
    return buildImageTagsFromLines_(number, [
      '종류=좌표평면',
      '식=' + yEquations[0],
      '표시=포물선 위 정사각형'
    ], [
      'template=parabola_inscribed_square',
      'equation=' + toImageEquation_(yEquations[0])
    ]);
  }

  if (/x축.*만나는.*두 점|두 점.*x축|꼭짓점/.test(problem) && yEquations.length >= 1) {
    return buildImageTagsFromLines_(number, [
      '종류=좌표평면',
      '식=' + yEquations[0],
      '표시=x축 교점과 꼭짓점'
    ], [
      'template=parabola_xintercepts_vertex_triangle',
      'equation=' + toImageEquation_(yEquations[0])
    ]);
  }

  return '';
}

function buildImageTagsFromLines_(number, koreanLines, englishLines) {
  return [
    '[이미지 필요' + number + ':'
  ].concat(koreanLines).concat([
    ']',
    '[IMAGE_PROMPT' + number + ':'
  ]).concat(englishLines).concat([
    ']'
  ]).join('\n');
}

function extractConcreteYEquationsForImage_(problem) {
  return unique_(extractMathExpressions_(problem)
    .map(expr => String(expr || '').trim())
    .filter(expr => /^y\s*=/.test(expr))
    .filter(expr => /x/.test(expr))
    .filter(expr => !hasUnresolvedEquationLetters_(expr)));
}

function extractConcreteAxisLinesForImage_(problem, axis) {
  const regex = axis === 'x'
    ? /^x\s*=\s*(-?\d+(?:\/\d+)?(?:\.\d+)?)$/
    : /^y\s*=\s*(-?\d+(?:\/\d+)?(?:\.\d+)?)$/;
  return unique_(extractMathExpressions_(problem)
    .map(expr => String(expr || '').trim())
    .map(expr => {
      const match = expr.match(regex);
      return match ? match[1] : '';
    })
    .filter(Boolean));
}

function toImageEquation_(equation) {
  return String(equation || '')
    .replace(/²/g, '^2')
    .replace(/−/g, '-')
    .replace(/\s+/g, ' ')
    .trim();
}

function buildFallbackGeometryTags_(problem, number) {
  const points = extractNamedPoints_(problem).slice(0, 8);
  const pointText = points.length ? points.join(',') : 'A,B,C,D';
  const shape = /사다리꼴/.test(problem) ? '사다리꼴' : (/삼각형/.test(problem) ? '삼각형' : '도형');
  const englishShape = shape === '사다리꼴' ? 'trapezoid' : (shape === '삼각형' ? 'triangle' : 'geometry_figure');

  return [
    '[이미지 필요' + number + ':',
    '종류=도형',
    '도형=' + shape,
    '점=' + pointText,
    '표시=점 이름, 변',
    ']',
    '[IMAGE_PROMPT' + number + ':',
    'type=geometry',
    'shape=' + englishShape,
    'points=' + pointText,
    'labels=' + pointText,
    ']'
  ].join('\n');
}

function extractMathExpressions_(text) {
  const expressions = [];
  const regex = /\[수식:\s*([^\]]+)\]/g;
  let match;
  while ((match = regex.exec(String(text || ''))) !== null) {
    const expr = String(match[1] || '').trim();
    if (expr && expressions.indexOf(expr) < 0) expressions.push(expr);
  }
  return expressions;
}

function extractCoordinatePoints_(text) {
  const points = [];
  const regex = /\((-?\d+(?:\/\d+)?),\s*(-?\d+(?:\/\d+)?)\)/g;
  let match;
  while ((match = regex.exec(String(text || ''))) !== null) {
    const point = '(' + match[1] + ',' + match[2] + ')';
    if (points.indexOf(point) < 0) points.push(point);
  }
  return points;
}

function extractNamedPoints_(text) {
  const names = [];
  const regex = /\b[A-Z]\b/g;
  let match;
  while ((match = regex.exec(String(text || ''))) !== null) {
    if (names.indexOf(match[0]) < 0) names.push(match[0]);
  }
  return names;
}

function applyImageRequirementsToPlan_(items, configuredImageCount) {
  const requiredCount = configuredImageCount === undefined || configuredImageCount === null
    ? getRequiredImageProblemCount_(items.length)
    : clampInteger_(configuredImageCount, 0, items.length);
  const selected = shuffle_(items.filter(item => isImageEligiblePlanItem_(item))).slice(0, requiredCount);
  const selectedByNumber = {};
  selected.forEach(item => selectedByNumber[Number(item.number)] = true);

  return items.map(item => {
    const imageRequired = Boolean(selectedByNumber[Number(item.number)]);
    return Object.assign({}, item, {
      imageRequired,
      imageKind: imageRequired ? getImageKindForWeakType_(item.weakType) : ''
    });
  });
}

function getRequiredImageProblemCount_(totalCount) {
  const count = Number(totalCount || 0);
  if (count <= 10) return 3;
  if (count <= 20) return 5;
  return 8;
}

function clampInteger_(value, min, max) {
  const number = Math.floor(Number(value || 0));
  return Math.max(min, Math.min(max, number));
}

function isImageFriendlyWeakType_(weakType) {
  return /(?:도형|넓이|삼각형|사각형|원|각|변|좌표평면|좌표|그래프|평행이동|대칭)/.test(String(weakType || ''));
}

function isImageEligiblePlanItem_(item) {
  const weakType = String(item && item.weakType || '');
  return isImageFriendlyWeakType_(weakType) && !isImageAnswerLeakRiskType_(weakType);
}

function isImageAnswerLeakRiskType_(weakType) {
  return /(?:사분면|지나지\s*않|지나는\s*사분면|그래프가\s*지나|위치\s*판단|부호\s*판단|증가|감소|최댓값|최솟값|해의\s*개수|교점의?\s*개수|개형\s*판단)/.test(String(weakType || ''));
}

function getImageKindForWeakType_(weakType) {
  const type = String(weakType || '');
  if (/(?:그래프|좌표|이차함수|함수|포물선|평행이동)/.test(type)) return 'coordinate_plane';
  return /(?:도형|넓이|삼각형|사각형|원|각|변)/.test(type)
    ? 'geometry'
    : 'coordinate_plane';
}

function getTwinProblemTotalCount_(weakTypeCount) {
  const count = Number(weakTypeCount || 0);
  if (count <= 2) return 10;
  if (count === 3) return 20;
  return 30;
}

function formatGeneratedProblems_(studentName, examName, plan, generatedProblems) {
  const byNumber = {};
  generatedProblems.forEach(item => {
    byNumber[Number(item.number)] = {
      problem: cleanGeneratedText_(item.problem || item.body || ''),
      answer: cleanGeneratedText_(item.answer || ''),
      solution: cleanGeneratedText_(item.solution || item.explanation || ''),
      body: cleanGeneratedText_(item.body || '')
    };
  });

  const lines = [
    studentName + ' - ' + examName + ' 쌍둥이 문항',
    '총 문항 수: ' + plan.totalCount,
    '약점 유형: ' + plan.weakTypes.join(', '),
    ''
  ];

  plan.items
    .slice()
    .sort((a, b) => a.number - b.number)
    .forEach(item => {
      const generated = byNumber[item.number];
      const body = generated
        ? formatProblemOnly_(item.number, generated.problem || generated.body || '')
        : '문항' + item.number + '. [생성 실패: AI 응답에서 해당 번호를 찾지 못했습니다.]';
      lines.push(body);
      lines.push('');
    });

  lines.push('');
  lines.push('[정답 및 해설]');
  lines.push('');

  plan.items
    .slice()
    .sort((a, b) => a.number - b.number)
    .forEach(item => {
      const generated = byNumber[item.number];
      lines.push('문항' + item.number + '.');
      if (generated) {
        lines.push(formatAnswerLine_(generated.answer));
        lines.push(formatSolutionLine_(generated.solution || generated.explanation || ''));
      } else {
        lines.push('정답: [생성 실패]');
        lines.push('해설: [생성 실패]');
      }
      lines.push('');
    });
  return lines.join('\n').trim();
}

function lookupWrongProblems_(examName, wrongNumbersText) {
  if (isPerfectScoreText_(wrongNumbersText)) return [];
  const requested = parseProblemNumbers_(wrongNumbersText);

  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEETS.PROBLEM_BANK);
  const bankRows = readObjects_(sheet)
    .map(item => item.rowObject)
    .filter(row => String(row['시험지 이름']) === String(examName));
  const byExactNumber = {};
  bankRows.forEach(row => {
    const number = normalizeProblemNumber_(row['문제번호']);
    if (number && !byExactNumber[number]) byExactNumber[number] = row;
  });

  const expandedRequested = expandRequestedProblemNumbers_(requested, byExactNumber);
  const missing = [];
  const matches = expandedRequested.map(requestedNumber => {
    const exactNumber = normalizeProblemNumber_(requestedNumber);
    const baseNumber = getBaseProblemNumber_(exactNumber);
    const row = byExactNumber[exactNumber] || byExactNumber[baseNumber];
    if (!row) {
      missing.push(requestedNumber);
      return null;
    }
    return {
      problemNumber: exactNumber,
      sourceProblemNumber: normalizeProblemNumber_(row['문제번호']),
      rawType: String(row['문제 유형'] || '').trim(),
      type: String(row['표준 문제 유형'] || row['문제 유형'] || '').trim(),
      unit1: String(row['상위 단원'] || '').trim(),
      unit2: String(row['하위 단원'] || '').trim(),
      answer: String(row['정답'] || '').trim()
    };
  }).filter(Boolean);

  if (missing.length) {
    throw new Error('문제은행에서 일부 오답 번호를 찾지 못했습니다. 요청: ' + missing.join(', '));
  }
  const missingTypes = matches.filter(item => !item.type).map(item => item.problemNumber);
  if (missingTypes.length) {
    throw new Error('문제 유형이 비어 있는 문제번호가 있습니다: ' + missingTypes.join(', '));
  }
  return matches;
}

function readTwinRules_() {
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEETS.TWIN_RULES);
  const rules = {};
  readObjects_(sheet).forEach(item => {
    const row = item.rowObject;
    const type = String(row['문제 유형'] || '').trim();
    if (!type) return;
    if (String(row['사용여부'] || 'TRUE').toUpperCase() === 'FALSE') return;
    rules[type] = {
      defaultCount: Number(row['기본문항수'] || 0),
      difficulty: String(row['난이도'] || '').trim(),
      rule: String(row['생성규칙'] || '').trim(),
      banned: String(row['금지사항'] || '').trim(),
      includeSolution: String(row['풀이포함여부'] || 'TRUE').toUpperCase() !== 'FALSE'
    };
  });
  return rules;
}

function readTypeMappings_() {
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEETS.TYPE_MAPPING);
  if (!sheet) return {};
  const mappings = {};
  readObjects_(sheet).forEach(item => {
    const row = item.rowObject;
    const rawType = String(row['원본 문제 유형'] || '').trim();
    if (!rawType) return;
    if (String(row['사용여부'] || 'TRUE').toUpperCase() === 'FALSE') return;
    const standardType = String(row['표준 문제 유형'] || rawType).trim();
    const key = buildTypeMappingKey_(rawType, row['상위 단원'], row['하위 단원']);
    mappings[key] = standardType;
  });
  return mappings;
}

function getStandardType_(rawType, unit1, unit2, mappings) {
  const originalType = String(rawType || '').trim();
  if (!originalType) return '';
  const typeMappings = mappings || readTypeMappings_();
  const exactKey = buildTypeMappingKey_(originalType, unit1, unit2);
  if (typeMappings[exactKey]) return typeMappings[exactKey];

  const looseKey = buildTypeMappingKey_(originalType, '', '');
  if (typeMappings[looseKey]) return typeMappings[looseKey];

  return originalType;
}

function buildTypeMappingKey_(rawType, unit1, unit2) {
  return [rawType, unit1, unit2]
    .map(value => String(value || '').trim())
    .join('||');
}

function upsertWrongHistory_(teacherSheetName, teacherRow, studentName, examName, wrongProblems, links) {
  const ss = SpreadsheetApp.getActive();
  const sheet = ss.getSheetByName(SHEETS.WRONG_HISTORY);
  const headers = getHeaderMap_(sheet);
  const existing = {};
  readObjects_(sheet).forEach(item => {
    existing[String(item.rowObject['중복키'])] = item.rowNumber;
  });

  const now = new Date();
  const rowsToAppend = [];
  const newProblemsForSummary = [];
  const historyProblems = wrongProblems.length ? wrongProblems : [{
    problemNumber: PERFECT_SCORE_TEXT,
    type: PERFECT_SCORE_TEXT,
    rawType: PERFECT_SCORE_TEXT,
    unit1: '',
    unit2: '',
    answer: '100점'
  }];
  historyProblems.forEach(problem => {
    const key = buildWrongHistoryKey_(studentName, examName, problem.problemNumber);
    const rowNumber = existing[key];
    const values = {
      '중복키': key,
      '기록일시': now,
      '선생님시트': teacherSheetName,
      '학생 이름': studentName,
      '시험지 이름': examName,
      '시험일': extractDateFromExamName_(examName),
      '문제번호': problem.problemNumber,
      '문제 유형': problem.type,
      '원본 문제 유형': problem.rawType || problem.type,
      '상위 단원': problem.unit1,
      '하위 단원': problem.unit2,
      '정답': problem.answer,
      '입력행': teacherRow
    };
    if (links && links.reportUrl) values['보고서 링크'] = links.reportUrl;
    if (links && links.twinUrl) values['쌍둥이 문항 링크'] = links.twinUrl;

    if (rowNumber) {
      setRowValues_(sheet, rowNumber, headers, values);
    } else {
      rowsToAppend.push(HEADERS.WRONG_HISTORY.map(header => values[header] || ''));
      if (!isPerfectHistoryRow_(values)) newProblemsForSummary.push(problem);
    }
  });

  if (rowsToAppend.length) {
    sheet.getRange(sheet.getLastRow() + 1, 1, rowsToAppend.length, HEADERS.WRONG_HISTORY.length).setValues(rowsToAppend);
  }
  if (newProblemsForSummary.length) {
    upsertWeaknessSummary_(teacherSheetName, studentName, examName, newProblemsForSummary);
  }
}

function buildStudentHistorySummary_(studentName, currentWrongProblems) {
  const summary = buildStudentWeaknessSummary_(studentName, currentWrongProblems);
  const baseSummary = summary.recordCount > 0
    ? summary
    : buildStudentHistorySummaryFromRaw_(studentName, currentWrongProblems);
  return Object.assign({}, baseSummary, buildStudentAssessmentSummary_(studentName));
}

function stripImageTags_(text) {
  return String(text || '')
    .replace(/\[(?:이미지\s*필요|IMAGE_PROMPT)\s*\d*\s*:[\s\S]*?\]/gi, '')
    .trim();
}

function buildStudentWeaknessSummary_(studentName, currentWrongProblems) {
  const ss = SpreadsheetApp.getActive();
  const sheet = ss.getSheetByName(SHEETS.WEAKNESS_SUMMARY);
  if (!sheet) return { recordCount: 0 };

  const currentKeys = {};
  currentWrongProblems.forEach(problem => {
    currentKeys[problem.type] = true;
    if (problem.unit1) currentKeys[problem.unit1] = true;
  });

  const rows = readObjects_(sheet)
    .map(item => item.rowObject)
    .filter(row => String(row['학생 이름']) === String(studentName));

  const monthSummary = {};
  const repeatedTypes = {};
  const repeatedUnits = {};
  rows.forEach(row => {
    const month = String(row['월'] || '').trim();
    const unit = String(row['상위 단원'] || '미분류').trim();
    const type = String(row['문제 유형'] || '미분류').trim();
    const count = Number(row['오답 횟수'] || 0);
    if (!monthSummary[month]) monthSummary[month] = {};
    if (!monthSummary[month][unit]) monthSummary[month][unit] = 0;
    monthSummary[month][unit] += count;
    repeatedTypes[type] = (repeatedTypes[type] || 0) + count;
    repeatedUnits[unit] = (repeatedUnits[unit] || 0) + count;
  });

  return {
    source: SHEETS.WEAKNESS_SUMMARY,
    recordCount: rows.length,
    currentWeakTypes: Object.keys(currentKeys),
    monthlyUnitWrongCounts: monthSummary,
    repeatedTypes: topCounts_(repeatedTypes, 10),
    repeatedUnits: topCounts_(repeatedUnits, 10)
  };
}

function buildStudentHistorySummaryFromRaw_(studentName, currentWrongProblems) {
  const ss = SpreadsheetApp.getActive();
  const sheet = ss.getSheetByName(SHEETS.WRONG_HISTORY);
  if (!sheet) return {};

  const currentKeys = {};
  currentWrongProblems.forEach(problem => {
    currentKeys[problem.type] = true;
    if (problem.unit1) currentKeys[problem.unit1] = true;
  });

  const rows = readObjects_(sheet)
    .map(item => item.rowObject)
    .filter(row => String(row['학생 이름']) === String(studentName));
  const wrongRows = rows.filter(row => !isPerfectHistoryRow_(row));

  const monthlyUnitTypes = {};
  const repeatedTypes = {};
  const repeatedUnits = {};
  wrongRows.forEach(row => {
    const month = getHistoryMonth_(row['시험일'], row['시험지 이름'], row['기록일시']);
    const unit = String(row['상위 단원'] || '미분류').trim();
    const type = String(row['문제 유형'] || '미분류').trim();
    if (!monthlyUnitTypes[month]) monthlyUnitTypes[month] = {};
    if (!monthlyUnitTypes[month][unit]) monthlyUnitTypes[month][unit] = {};
    monthlyUnitTypes[month][unit][type] = true;
    repeatedTypes[type] = (repeatedTypes[type] || 0) + 1;
    repeatedUnits[unit] = (repeatedUnits[unit] || 0) + 1;
  });

  const monthSummary = {};
  Object.keys(monthlyUnitTypes).sort().forEach(month => {
    monthSummary[month] = {};
    Object.keys(monthlyUnitTypes[month]).forEach(unit => {
      monthSummary[month][unit] = Object.keys(monthlyUnitTypes[month][unit]).length;
    });
  });

  return {
    source: SHEETS.WRONG_HISTORY,
    recordCount: wrongRows.length,
    currentWeakTypes: Object.keys(currentKeys),
    monthlyUnitWeakTypeCounts: monthSummary,
    repeatedTypes: topCounts_(repeatedTypes, 10),
    repeatedUnits: topCounts_(repeatedUnits, 10)
  };
}

function buildStudentAssessmentSummary_(studentName) {
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEETS.WRONG_HISTORY);
  if (!sheet) return { assessmentCount: 0, perfectScoreCount: 0, perfectScoreExamNames: [] };

  const rows = readObjects_(sheet)
    .map(item => item.rowObject)
    .filter(row => String(row['학생 이름']) === String(studentName));
  const assessments = {};
  const perfectScores = {};
  rows.forEach(row => {
    const examName = String(row['시험지 이름'] || '').trim();
    const examDate = String(row['시험일'] || '').trim();
    const key = [examName, examDate].join('||');
    if (!examName) return;
    assessments[key] = true;
    if (isPerfectHistoryRow_(row)) perfectScores[key] = examName;
  });

  return {
    assessmentCount: Object.keys(assessments).length,
    perfectScoreCount: Object.keys(perfectScores).length,
    perfectScoreExamNames: Object.keys(perfectScores).map(key => perfectScores[key]).slice(-10)
  };
}

function isPerfectHistoryRow_(row) {
  return isPerfectScoreText_(row && (row['문제번호'] || row['문제 유형']));
}

function buildWrongHistoryKey_(studentName, examName, problemNumber) {
  return [studentName, examName, normalizeProblemNumber_(problemNumber)].map(value => String(value || '').trim()).join('||');
}

function upsertWeaknessSummary_(teacherSheetName, studentName, examName, wrongProblems) {
  const ss = SpreadsheetApp.getActive();
  const sheet = ss.getSheetByName(SHEETS.WEAKNESS_SUMMARY);
  const headers = getHeaderMap_(sheet);
  const existing = {};
  readObjects_(sheet).forEach(item => {
    existing[String(item.rowObject['요약키'])] = {
      rowNumber: item.rowNumber,
      rowObject: item.rowObject
    };
  });

  const examDate = extractDateFromExamName_(examName) || Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
  const month = examDate.slice(0, 7);
  const grouped = {};
  wrongProblems.forEach(problem => {
    const key = buildWeaknessSummaryKey_(studentName, month, problem.unit1, problem.unit2, problem.type);
    if (!grouped[key]) {
      grouped[key] = {
        key,
        studentName,
        month,
        unit1: problem.unit1 || '미분류',
        unit2: problem.unit2 || '미분류',
        type: problem.type || '미분류',
        count: 0,
        examDate,
        examName,
        teacherSheetName
      };
    }
    grouped[key].count += 1;
  });

  const rowsToAppend = [];
  Object.keys(grouped).forEach(key => {
    const item = grouped[key];
    const found = existing[key];
    if (found) {
      const currentCount = Number(found.rowObject['오답 횟수'] || 0);
      const firstDate = String(found.rowObject['첫 오답일'] || item.examDate);
      const recentDate = maxDateText_(String(found.rowObject['최근 오답일'] || ''), item.examDate);
      setRowValues_(sheet, found.rowNumber, headers, {
        '오답 횟수': currentCount + item.count,
        '첫 오답일': minDateText_(firstDate, item.examDate),
        '최근 오답일': recentDate,
        '시험 횟수': Number(found.rowObject['시험 횟수'] || 0) + 1,
        '최근 시험지 이름': item.examName,
        '선생님시트': item.teacherSheetName
      });
    } else {
      rowsToAppend.push([
        item.key,
        item.studentName,
        item.month,
        item.unit1,
        item.unit2,
        item.type,
        item.count,
        item.examDate,
        item.examDate,
        1,
        item.examName,
        item.teacherSheetName
      ]);
    }
  });

  if (rowsToAppend.length) {
    sheet.getRange(sheet.getLastRow() + 1, 1, rowsToAppend.length, HEADERS.WEAKNESS_SUMMARY.length).setValues(rowsToAppend);
  }
}

function buildWeaknessSummaryKey_(studentName, month, unit1, unit2, type) {
  return [studentName, month, unit1 || '미분류', unit2 || '미분류', type || '미분류']
    .map(value => String(value || '').trim())
    .join('||');
}

function extractDateFromExamName_(examName) {
  const match = String(examName || '').match(/(\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})/);
  if (!match) return '';
  const year = 2000 + Number(match[1]);
  const month = String(Number(match[2])).padStart(2, '0');
  const day = String(Number(match[3])).padStart(2, '0');
  return year + '-' + month + '-' + day;
}

function getHistoryMonth_(examDate, examName, fallbackDate) {
  const dateText = String(examDate || extractDateFromExamName_(examName) || '');
  if (/^\d{4}-\d{2}-\d{2}$/.test(dateText)) return dateText.slice(0, 7);
  const date = fallbackDate ? new Date(fallbackDate) : new Date();
  return Utilities.formatDate(date, Session.getScriptTimeZone(), 'yyyy-MM');
}

function topCounts_(counts, limit) {
  return Object.keys(counts)
    .map(key => ({ name: key, count: counts[key] }))
    .sort((a, b) => b.count - a.count)
    .slice(0, limit);
}

function minDateText_(a, b) {
  if (!a) return b || '';
  if (!b) return a || '';
  return String(a) <= String(b) ? a : b;
}

function maxDateText_(a, b) {
  if (!a) return b || '';
  if (!b) return a || '';
  return String(a) >= String(b) ? a : b;
}

function saveTextToStudentFolder_(studentName, fileName, text) {
  const rootId = getDriveRootFolderId_();
  if (!rootId) throw new Error('관리자_설정에 Drive루트폴더ID가 없습니다.');

  const root = DriveApp.getFolderById(rootId);
  const studentFolder = getOrCreateChildFolder_(root, sanitizeFileName_(studentName));
  const existing = studentFolder.getFilesByName(fileName);
  while (existing.hasNext()) {
    existing.next().setTrashed(true);
  }
  const file = studentFolder.createFile(fileName, text, MimeType.PLAIN_TEXT);
  return file.getUrl();
}

function getDriveRootFolderId_() {
  const configs = readAdminConfigs_();
  const withFolder = configs.find(config => config.driveRootFolderId);
  return withFolder ? withFolder.driveRootFolderId : '';
}

function enqueueTasks_(tasks) {
  if (!tasks.length) return 0;
  const ss = SpreadsheetApp.getActive();
  const sheet = ss.getSheetByName(SHEETS.QUEUE);
  const now = new Date();
  const existingKeys = getOpenQueueKeys_(sheet);
  const uniqueTasks = tasks.filter(task => {
    const key = buildQueueKey_(task.taskType, task.targetSheet, task.targetRow);
    if (existingKeys[key]) return false;
    existingKeys[key] = true;
    return true;
  });
  if (!uniqueTasks.length) return 0;

  const values = uniqueTasks.map(task => [
    Utilities.getUuid(),
    task.taskType,
    task.targetSheet,
    task.targetRow,
    QUEUE_STATUS.PENDING,
    0,
    now,
    '',
    now,
    '',
    JSON.stringify(task.payload)
  ]);
  sheet.getRange(sheet.getLastRow() + 1, 1, values.length, HEADERS.QUEUE.length).setValues(values);
  return uniqueTasks.length;
}

function getOpenQueueKeys_(queueSheet) {
  const keys = {};
  readObjects_(queueSheet)
    .filter(item => item.rowObject['상태'] === QUEUE_STATUS.PENDING || item.rowObject['상태'] === QUEUE_STATUS.RUNNING)
    .forEach(item => {
      const key = buildQueueKey_(
        item.rowObject['작업종류'],
        item.rowObject['대상시트'],
        item.rowObject['대상행']
      );
      keys[key] = true;
    });
  return keys;
}

function buildQueueKey_(taskType, targetSheet, targetRow) {
  return [taskType, targetSheet, targetRow].map(value => String(value || '')).join('||');
}

function getActiveTeacherSheet_() {
  const sheet = SpreadsheetApp.getActiveSheet();
  if (RESERVED_SHEETS.indexOf(sheet.getName()) >= 0) {
    throw new Error('예약 시트에서는 선생님용 작업을 실행할 수 없습니다: ' + sheet.getName());
  }
  ensureHeaderIncludes_(sheet, HEADERS.TEACHER);
  applyExamDropdownToSheet_(sheet);
  return sheet;
}

function getQueueTargetSheet_(masterSpreadsheet, targetSheetName, payload, context) {
  const remote = getRemoteTargetInfo_(targetSheetName, payload);
  if (remote) {
    const teacherSpreadsheet = SpreadsheetApp.openById(remote.fileId);
    const sheet = teacherSpreadsheet.getSheetByName(remote.sheetName);
    if (!sheet) {
      throw new Error((context ? context + ': ' : '') + '선생님 파일에서 "' + remote.sheetName + '" 시트를 찾을 수 없습니다. 파일: ' + teacherSpreadsheet.getName());
    }
    return sheet;
  }

  const sheet = masterSpreadsheet.getSheetByName(targetSheetName);
  if (!sheet) {
    throw new Error((context ? context + ': ' : '') + '"' + targetSheetName + '" 시트를 찾을 수 없습니다.');
  }
  return sheet;
}

function getRemoteTargetInfo_(targetSheetName, payload) {
  if (payload && payload.teacherFileId && payload.teacherSheetName) {
    return {
      fileId: String(payload.teacherFileId).trim(),
      sheetName: String(payload.teacherSheetName).trim()
    };
  }

  const text = String(targetSheetName || '');
  const parts = text.split('::');
  if (parts.length >= 3 && parts[0] === 'REMOTE') {
    return {
      fileId: parts[1],
      sheetName: parts.slice(2).join('::')
    };
  }
  return null;
}

function getSheetScopeAliases_(currentSheetName) {
  const aliases = {};
  const original = String(currentSheetName || '').trim();
  if (original) aliases[original] = true;

  const remote = getRemoteTargetInfo_(original, null);
  if (remote && remote.sheetName) aliases[remote.sheetName] = true;
  return aliases;
}

function applyExamDropdownToSheet_(sheet) {
  ensureHeaderIncludes_(sheet, HEADERS.TEACHER);
  const ss = SpreadsheetApp.getActive();
  const listSheet = ss.getSheetByName(SHEETS.EXAM_LIST) || ensureSheet_(ss, SHEETS.EXAM_LIST, HEADERS.EXAM_LIST);
  const headers = getHeaderMap_(sheet);
  const examColumn = headers['시험지 이름'];
  if (!examColumn) throw new Error(sheet.getName() + ' 시트에 시험지 이름 열이 없습니다.');

  const listLastRow = Math.max(2, listSheet.getLastRow());
  const listRange = listSheet.getRange(2, 1, listLastRow - 1, 1);
  const validation = SpreadsheetApp.newDataValidation()
    .requireValueInRange(listRange, true)
    .setAllowInvalid(false)
    .build();
  sheet.getRange(2, examColumn, Math.max(sheet.getMaxRows() - 1, 1), 1).setDataValidation(validation);
}

function shouldSkipCompletedTask_(queueRow, payload) {
  const taskType = queueRow['작업종류'];
  const sheetName = queueRow['대상시트'];
  const rowNumber = Number(queueRow['대상행']);
  const ss = SpreadsheetApp.getActive();

  if (taskType === TASK_TYPES.PROBLEM_ANALYSIS) {
    const sheet = ss.getSheetByName(SHEETS.PROBLEM_BANK);
    const headers = getHeaderMap_(sheet);
    return (payload.problemRows || []).every(row => {
      const type = sheet.getRange(row.rowNumber, headers['문제 유형']).getValue();
      const answer = sheet.getRange(row.rowNumber, headers['정답']).getValue();
      return type && answer;
    });
  }

  if (taskType === TASK_TYPES.STUDENT_REPORT) {
    const sheet = getQueueTargetSheet_(ss, sheetName, payload, '분석 보고서 완료 여부 확인');
    const headers = getHeaderMap_(sheet);
    return Boolean(sheet.getRange(rowNumber, headers['분석 보고서']).getValue());
  }

  if (taskType === TASK_TYPES.SIMILAR_PROBLEMS) {
    const sheet = getQueueTargetSheet_(ss, sheetName, payload, '쌍둥이 문항 완료 여부 확인');
    const headers = getHeaderMap_(sheet);
    return Boolean(sheet.getRange(rowNumber, headers['쌍둥이 문항']).getValue());
  }

  return false;
}

function isTeacherTask_(taskType) {
  return taskType === TASK_TYPES.STUDENT_REPORT || taskType === TASK_TYPES.SIMILAR_PROBLEMS;
}

function isSheetCooldownReady_(sheetName, now) {
  const last = Number(PropertiesService.getScriptProperties().getProperty(buildSheetCooldownKey_(sheetName)) || 0);
  return now.getTime() - last >= getSheetCooldownMs_(sheetName);
}

function markSheetProcessed_(sheetName) {
  PropertiesService.getScriptProperties().setProperty(buildSheetCooldownKey_(sheetName), String(Date.now()));
}

function buildSheetCooldownKey_(sheetName) {
  return 'LAST_PROCESSED_AT__' + String(sheetName || '').trim();
}

function getSheetCooldownMs_(sheetName) {
  const configs = readAdminConfigs_().filter(config => matchesSheetScope_(config.sheetScope, sheetName));
  if (!configs.length) return DEFAULT_STUDENT_COOLDOWN_MS;
  const configured = Math.max.apply(null, configs.map(config => Number(config.delayMs || 0)));
  return Math.max(DEFAULT_STUDENT_COOLDOWN_MS, configured);
}

function ensureSheet_(ss, name, headers) {
  const sheet = ss.getSheetByName(name) || ss.insertSheet(name);
  ensureHeaderIncludes_(sheet, headers);
  sheet.setFrozenRows(1);
  return sheet;
}

function ensureHeaderIncludes_(sheet, requiredHeaders) {
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, requiredHeaders.length).setValues([requiredHeaders]);
    return;
  }
  const width = Math.max(sheet.getLastColumn(), requiredHeaders.length);
  const current = sheet.getRange(1, 1, 1, width).getValues()[0].map(String);
  const missing = requiredHeaders.filter(header => current.indexOf(header) < 0);
  if (missing.length) {
    sheet.getRange(1, current.filter(Boolean).length + 1, 1, missing.length).setValues([missing]);
  }
}

function clearSheetBody_(sheet) {
  const lastRow = sheet.getLastRow();
  const lastColumn = sheet.getLastColumn();
  if (lastRow > 1 && lastColumn > 0) {
    sheet.getRange(2, 1, lastRow - 1, lastColumn).clearContent();
  }
}

function readObjects_(sheet) {
  const lastRow = sheet.getLastRow();
  const lastColumn = sheet.getLastColumn();
  if (lastRow < 2 || lastColumn < 1) return [];
  const values = sheet.getRange(1, 1, lastRow, lastColumn).getValues();
  const headers = values[0].map(header => String(header).trim());
  const rows = [];
  for (let r = 1; r < values.length; r++) {
    const object = {};
    headers.forEach((header, index) => {
      if (header) object[header] = values[r][index];
    });
    if (Object.keys(object).some(key => object[key] !== '' && object[key] !== null)) {
      rows.push({ rowNumber: r + 1, rowObject: object });
    }
  }
  return rows;
}

function getHeaderMap_(sheet) {
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const map = {};
  headers.forEach((header, index) => {
    if (header) map[String(header).trim()] = index + 1;
  });
  return map;
}

function readRowObject_(sheet, rowNumber) {
  const lastColumn = sheet.getLastColumn();
  const headers = sheet.getRange(1, 1, 1, lastColumn).getValues()[0].map(header => String(header).trim());
  const values = sheet.getRange(rowNumber, 1, 1, lastColumn).getValues()[0];
  const object = {};
  headers.forEach((header, index) => {
    if (header) object[header] = values[index];
  });
  return object;
}

function setRowValues_(sheet, rowNumber, headerMap, valuesByHeader) {
  Object.keys(valuesByHeader).forEach(header => {
    if (!headerMap[header]) return;
    sheet.getRange(rowNumber, headerMap[header]).setValue(valuesByHeader[header]);
  });
}

function parseProblemNumbers_(text) {
  return String(text || '')
    .split(',')
    .map(part => normalizeProblemNumber_(part))
    .filter(Boolean);
}

function isPerfectScoreText_(text) {
  const normalized = String(text || '').replace(/\s+/g, '');
  return normalized === '오답없음(100점)' ||
    normalized === '오답없음' ||
    normalized === '100점' ||
    normalized === '틀린문제없음';
}

function normalizeProblemNumber_(value) {
  if (Object.prototype.toString.call(value) === '[object Date]' && !isNaN(value.getTime())) {
    return (value.getMonth() + 1) + ',' + value.getDate();
  }
  const text = String(value || '')
    .replace(/\s+/g, '')
    .replace(/[–—]/g, '-')
    .trim();
  const dateMatch = text.match(/^(?:Sun|Mon|Tue|Wed|Thu|Fri|Sat)(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)(\d{1,2})\d{4}/i);
  if (!dateMatch) return text;
  const months = {
    jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6,
    jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12
  };
  return months[dateMatch[1].toLowerCase()] + ',' + Number(dateMatch[2]);
}

function expandRequestedProblemNumbers_(requestedNumbers, byExactNumber) {
  const expanded = [];
  const seen = {};
  requestedNumbers.forEach(number => {
    const normalized = normalizeProblemNumber_(number);
    if (!normalized) return;
    const children = isBaseProblemNumber_(normalized)
      ? findChildProblemNumbers_(normalized, byExactNumber)
      : [];
    const numbersToAdd = children.length ? children : [normalized];
    numbersToAdd.forEach(item => {
      if (seen[item]) return;
      seen[item] = true;
      expanded.push(item);
    });
  });
  return expanded;
}

function findChildProblemNumbers_(baseNumber, byExactNumber) {
  const prefix = normalizeProblemNumber_(baseNumber) + '-(';
  return Object.keys(byExactNumber)
    .filter(number => number.indexOf(prefix) === 0)
    .sort(compareProblemNumbers_);
}

function isBaseProblemNumber_(value) {
  return /^\d+$/.test(normalizeProblemNumber_(value));
}

function compareProblemNumbers_(a, b) {
  const aBase = Number(getBaseProblemNumber_(a));
  const bBase = Number(getBaseProblemNumber_(b));
  if (aBase !== bBase) return aBase - bBase;
  return getSubProblemIndex_(a) - getSubProblemIndex_(b);
}

function getSubProblemIndex_(value) {
  const match = normalizeProblemNumber_(value).match(/-\((\d+)\)/);
  return match ? Number(match[1]) : 0;
}

function getBaseProblemNumber_(value) {
  const text = normalizeProblemNumber_(value);
  const match = text.match(/^(\d+)/);
  return match ? match[1] : text;
}

function normalizeConfidence_(value) {
  const text = String(value || '').trim().toUpperCase();
  if (text === 'HIGH' || text === 'MEDIUM' || text === 'LOW') return text;
  return text ? 'LOW' : '';
}

function normalizeFeatureName_(value) {
  const text = String(value || '').trim();
  const aliases = {
    '문제풀이': TASK_TYPES.PROBLEM_ANALYSIS,
    '문제은행': TASK_TYPES.PROBLEM_ANALYSIS,
    '분석': TASK_TYPES.PROBLEM_ANALYSIS,
    '보고서': TASK_TYPES.STUDENT_REPORT,
    '분석보고서': TASK_TYPES.STUDENT_REPORT,
    '문제생성': TASK_TYPES.SIMILAR_PROBLEMS,
    '쌍둥이': TASK_TYPES.SIMILAR_PROBLEMS,
    '쌍둥이문항': TASK_TYPES.SIMILAR_PROBLEMS
  };
  return aliases[text] || text;
}

function extractDriveFileId_(url) {
  const text = String(url || '');
  const patterns = [
    /\/d\/([a-zA-Z0-9_-]+)/,
    /id=([a-zA-Z0-9_-]+)/,
    /^([a-zA-Z0-9_-]{20,})$/
  ];
  for (let i = 0; i < patterns.length; i++) {
    const match = text.match(patterns[i]);
    if (match) return match[1];
  }
  return '';
}

function extractGeminiText_(json) {
  const parts = (((json || {}).candidates || [])[0] || {}).content || {};
  return (parts.parts || [])
    .map(part => part.text || '')
    .join('\n')
    .trim();
}

function parseJsonArray_(text) {
  const cleaned = String(text || '')
    .replace(/^```json\s*/i, '')
    .replace(/^```\s*/i, '')
    .replace(/```$/i, '')
    .trim();
  const start = cleaned.indexOf('[');
  const end = cleaned.lastIndexOf(']');
  if (start < 0 || end < start) throw new Error('AI 응답에서 JSON 배열을 찾지 못했습니다: ' + cleaned.slice(0, 300));
  const jsonText = cleaned.slice(start, end + 1);
  try {
    return JSON.parse(jsonText);
  } catch (firstErr) {
    const escapedJsonText = jsonText.replace(/\\(?!["\\/bfnrtu])/g, '\\\\');
    try {
      return JSON.parse(escapedJsonText);
    } catch (secondErr) {
      const strippedJsonText = jsonText.replace(/\\(?!["\\/bfnrtu])/g, '');
      try {
        return JSON.parse(strippedJsonText);
      } catch (thirdErr) {
        throw new Error('AI 응답 JSON 파싱 실패: ' + thirdErr.message + ' / 원문 일부: ' + jsonText.slice(0, 500));
      }
    }
  }
}

function parseGeneratedProblemArray_(text, planItems) {
  const expectedNumbers = (planItems || []).map(item => Number(item.number));
  const delimited = parseGeneratedProblemDelimited_(text);
  if (delimited.length) return remapGeneratedNumbersIfNeeded_(delimited, expectedNumbers);

  try {
    const parsed = parseJsonArray_(text);
    const normalized = parsed
      .map((item, index) => normalizeGeneratedProblemItem_(item, index + 1))
      .filter(item => item && item.number && (item.problem || item.body || item.answer || item.solution));
    return remapGeneratedNumbersIfNeeded_(normalized, expectedNumbers);
  } catch (err) {
    const recovered = parseGeneratedProblemFallback_(text);
    if (recovered.length) return remapGeneratedNumbersIfNeeded_(recovered, expectedNumbers);
    throw err;
  }
}

function parseGeneratedProblemDelimited_(text) {
  const source = String(text || '').replace(/\r/g, '').trim();
  if (!source) return [];

  const results = [];
  const blockRegex = /===\s*\uBB38\uD56D_START\s*===([\s\S]*?)===\s*\uBB38\uD56D_END\s*===/g;
  let match;
  while ((match = blockRegex.exec(source)) !== null) {
    const item = parseGeneratedProblemBlock_(match[1]);
    if (item) results.push(item);
  }
  return results;
}

function parseGeneratedProblemBlock_(block) {
  const text = String(block || '').trim();
  const numberMatch = text.match(/\uBC88\uD638\s*:\s*(\d+)/);
  if (!numberMatch) return null;

  const problem = extractLabeledSection_(text, '\uBB38\uC81C', ['\uC815\uB2F5', '\uD574\uC124']);
  const answer = cleanGeneratedAnswerOrSolution_(extractLabeledSection_(text, '\uC815\uB2F5', ['\uD574\uC124']));
  const solution = cleanGeneratedAnswerOrSolution_(extractLabeledSection_(text, '\uD574\uC124', []));
  const body = [problem, answer ? '정답: ' + answer : '', solution ? '해설: ' + solution : '']
    .filter(Boolean)
    .join('\n');

  return {
    number: Number(numberMatch[1]),
    problem,
    answer,
    solution,
    body
  };
}

function extractLabeledSection_(text, label, nextLabels) {
  const startRegex = new RegExp(label + '\\s*:\\s*');
  const startMatch = text.match(startRegex);
  if (!startMatch) return '';
  const start = startMatch.index + startMatch[0].length;
  let end = text.length;
  (nextLabels || []).forEach(nextLabel => {
    const nextRegex = new RegExp('\\n\\s*' + nextLabel + '\\s*:\\s*');
    const nextMatch = text.slice(start).match(nextRegex);
    if (nextMatch) {
      end = Math.min(end, start + nextMatch.index);
    }
  });
  return text.slice(start, end).trim();
}

function normalizeGeneratedProblemItem_(item, fallbackNumber) {
  if (!item) return null;
  if (typeof item === 'string') {
    return splitGeneratedBody_(fallbackNumber, item);
  }

  const number = Number(
    item.number ||
    item.no ||
    item.problemNumber ||
    item['문항번호'] ||
    item['문항 번호'] ||
    item['번호'] ||
    item['문항'] ||
    fallbackNumber
  );
  const problem = String(
    item.problem ||
    item.body ||
    item.question ||
    item['문제'] ||
    item['문항내용'] ||
    item['문항 내용'] ||
    item['본문'] ||
    ''
  );
  const answer = String(item.answer || item['정답'] || '');
  const solution = String(
    item.solution ||
    item.explanation ||
    item['해설'] ||
    item['풀이'] ||
    ''
  );
  const body = String(item.body || item['본문'] || '');
  return { number, problem, answer, solution, body };
}

function parseGeneratedProblemFallback_(text) {
  const cleaned = String(text || '')
    .replace(/^```json\s*/i, '')
    .replace(/^```\s*/i, '')
    .replace(/```$/i, '')
    .replace(/\r/g, '')
    .trim();

  const fromJsonish = parseGeneratedProblemJsonish_(cleaned);
  if (fromJsonish.length) return fromJsonish;
  return parseGeneratedProblemPlainText_(cleaned);
}

function parseGeneratedProblemJsonish_(text) {
  const chunks = text.split(/\{\s*"number"\s*:/).slice(1);
  const results = [];
  chunks.forEach(chunk => {
    const numberMatch = chunk.match(/^\s*(\d+)/);
    if (!numberMatch) return;
    const bodyKeyIndex = chunk.indexOf('"body"');
    if (bodyKeyIndex < 0) return;
    const colonIndex = chunk.indexOf(':', bodyKeyIndex);
    const firstQuoteIndex = chunk.indexOf('"', colonIndex + 1);
    if (colonIndex < 0 || firstQuoteIndex < 0) return;

    let body = chunk.slice(firstQuoteIndex + 1);
    const boundary = body.search(/"\s*,?\s*\}\s*,?\s*(?:\{\s*"number"|\])/);
    if (boundary >= 0) body = body.slice(0, boundary);
    body = body
      .replace(/\\n/g, '\n')
      .replace(/\\"/g, '"')
      .replace(/\\(?!["\\/bfnrtu])/g, '')
      .trim();
    if (body) {
      results.push(splitGeneratedBody_(Number(numberMatch[1]), body));
    }
  });
  return results;
}

function parseGeneratedProblemPlainText_(text) {
  const regex = /\uBB38\uD56D\s*(\d+)\s*\./g;
  const matches = [];
  let match;
  while ((match = regex.exec(text)) !== null) {
    matches.push({ number: Number(match[1]), index: match.index });
  }
  return matches.map((item, index) => {
    const next = matches[index + 1];
    const body = text.slice(item.index, next ? next.index : text.length).trim();
    return splitGeneratedBody_(item.number, body);
  }).filter(item => item.body);
}

function remapGeneratedNumbersIfNeeded_(items, expectedNumbers) {
  if (!items.length || !expectedNumbers || !expectedNumbers.length) return items;
  const expectedSet = {};
  expectedNumbers.forEach(number => expectedSet[Number(number)] = true);
  const matchingCount = items.filter(item => expectedSet[Number(item.number)]).length;
  if (matchingCount === items.length && matchingCount === expectedNumbers.length) return items;

  return items.map((item, index) => {
    const remappedNumber = expectedNumbers[index];
    if (!remappedNumber) return item;
    return {
      number: remappedNumber,
      problem: item.problem,
      answer: item.answer,
      solution: item.solution,
      body: item.body
    };
  });
}

function splitGeneratedBody_(number, body) {
  const text = String(body || '').trim();
  const answerMatch = text.match(/\n\s*\uC815\uB2F5\s*:/);
  const solutionMatch = text.match(/\n\s*(?:\uD480\uC774|\uD574\uC124)\s*:/);
  if (!answerMatch && !solutionMatch) {
    return { number, problem: text, answer: '', solution: '', body: text };
  }

  const firstSplitIndex = Math.min(
    answerMatch ? answerMatch.index : text.length,
    solutionMatch ? solutionMatch.index : text.length
  );
  const problem = text.slice(0, firstSplitIndex).trim();
  let answer = '';
  let solution = '';

  if (answerMatch) {
    const answerStart = answerMatch.index + answerMatch[0].length;
    const answerEnd = solutionMatch && solutionMatch.index > answerMatch.index ? solutionMatch.index : text.length;
    answer = cleanGeneratedAnswerOrSolution_(text.slice(answerStart, answerEnd));
  }
  if (solutionMatch) {
    const solutionStart = solutionMatch.index + solutionMatch[0].length;
    solution = cleanGeneratedAnswerOrSolution_(text.slice(solutionStart));
  }

  return { number, problem, answer, solution, body: text };
}

function cleanGeneratedAnswerOrSolution_(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  if (/^(?:\uC815\uB2F5|\uD574\uC124|\uD480\uC774)\s*:?\s*$/.test(text)) return '';
  return cleanGeneratedText_(text
    .replace(/^(?:\uC815\uB2F5|\uD574\uC124|\uD480\uC774)\s*:\s*/, '')
    .trim());
}

function cleanGeneratedText_(value) {
  return String(value || '')
    .replace(/\\Rightarrow/g, '⇒')
    .replace(/\\rightarrow/g, '→')
    .replace(/\\leftarrow/g, '←')
    .replace(/\\pm/g, '±')
    .replace(/\\times/g, '×')
    .replace(/\\cdot/g, '·')
    .replace(/\\leq/g, '≤')
    .replace(/\\geq/g, '≥')
    .replace(/\\neq(?![A-Za-z])/g, '≠')
    .replace(/\\sqrt\s*\{([^}]+)\}/g, '√$1')
    .replace(/\\frac\s*\{([^}]+)\}\s*\{([^}]+)\}/g, '$1/$2')
    .replace(/\$([^$]+)\$/g, '$1')
    .replace(/\\\(([\s\S]*?)\\\)/g, '$1')
    .trim();
}

function hasDraftLeakText_(item, planItem) {
  const text = [
    item && item.problem,
    item && item.answer,
    item && item.solution,
    item && item.body
  ].join('\n');
  return hasKoreanDraftLeakText_(text) || hasEnglishMetaText_(text) || hasOutOfScopeAdvancedMathText_(text, planItem);
}

function hasKoreanDraftLeakText_(text) {
  return /(?:잠시만|다시\s*시도|문제\s*설정을?\s*변경|계산이\s*깔끔하게|역설계|내가\s*만든\s*문제|여전히\s*정수로|다시\s*문제\s*설정|다시\s*조정|조화를\s*위해|정답이?\s*\d+\s*이?\s*되려면|다른\s*점을?\s*사용|문제에\s*주어진\s*정답|혼동했|착각했|재검토|대안\s*문제|실패한\s*계산|수정한\s*과정|정답을\s*다시\s*확인|다시\s*계산|올바른\s*정답을\s*도출|오답\s*목록|생성\s*규칙|제가|저의|제가\s*제시한|제\s*계산|현재\s*문제|원본\s*문제|정답을\s*\d+\s*으로\s*기재|문제와\s*풀이\s*기준|핵심\s*개념과\s*풀이\s*전략|답이\s*\d+\s*인\s*것은\s*문제가\s*없)/.test(String(text || '')) ||
    /`[^`]+`/.test(String(text || '')) ||
    /\(\s*(?:정답|오답|다시|제가|현재|생성|원본)[\s\S]{20,}\)/.test(String(text || ''));
}

function hasEnglishMetaText_(text) {
  const withoutImagePrompt = String(text || '').replace(/\[IMAGE_PROMPT\d*\s*:[\s\S]*?\]/g, '');
  return /\b(?:Wait|I might|I may|Let me|re-?evaluate|confusing|confused|different type|interpretation|original problem|common interpretation|Scenario|Concept|Difficulty|Details|Let's|try again|adjust|revise|my answer)\b/i.test(withoutImagePrompt);
}

function hasOutOfMiddleSchoolMathText_(text) {
  const source = String(text || '').replace(/\[IMAGE_PROMPT\d*\s*:[\s\S]*?\]/g, '');
  return /(?:정적분|부정적분|적분|미분|도함수|극한|고등학교\s*과정|대학\s*과정|∫|∂|\bdx\b|\blim\b|\bintegral\b|\bderivative\b)/i.test(source);
}

function hasOutOfScopeAdvancedMathText_(text, planItem) {
  if (!hasOutOfMiddleSchoolMathText_(text)) return false;
  const level = String(planItem && planItem.curriculumLevel || 'UNKNOWN');
  if (level === 'MIDDLE') return true;
  if (level === 'HIGH') return false;
  return !isAdvancedMathPlanItem_(planItem);
}

function isAdvancedMathPlanItem_(planItem) {
  const source = [
    planItem && planItem.weakType,
    planItem && planItem.unit1,
    planItem && planItem.unit2
  ].join(' ');
  return /(?:정적분|부정적분|적분|미분|도함수|극한|미적분|수학\s*(?:Ⅱ|II|2))/i.test(source);
}

function formatProblemOnly_(number, problem) {
  const cleaned = String(problem || '')
    .replace(/^[^\n.]{0,20}\d+\.\s*/, '')
    .replace(/^\s*\d+\.\s*/, '')
    .trim();
  const imageTags = [];
  const body = cleaned
    .replace(/\[(?:이미지 필요|IMAGE_PROMPT)\d*\s*:[\s\S]*?\]/g, tag => {
      imageTags.push(tag);
      return '';
    })
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  const imageText = unique_(imageTags).join(' ');
  return imageText
    ? ('문항' + number + '. ' + imageText + '\n' + body).trim()
    : ('문항' + number + '. ' + body).trim();
}

function formatAnswerLine_(answer) {
  const text = String(answer || '').trim();
  if (!text) return '정답: [정답 누락]';
  return /^\uC815\uB2F5\s*:/.test(text) ? text : '정답: ' + text;
}

function formatSolutionLine_(solution) {
  const text = String(solution || '').trim();
  if (!text) return '해설: [해설 누락]';
  return /^(?:\uD574\uC124|\uD480\uC774)\s*:/.test(text)
    ? text.replace(/^\uD480\uC774\s*:/, '해설:')
    : '해설: ' + text;
}

function readDriveTextFromUrl_(url) {
  if (!url) return '';
  const fileId = extractDriveFileId_(url);
  if (!fileId) return '';
  try {
    return DriveApp.getFileById(fileId).getBlob().getDataAsString('UTF-8').slice(0, 12000);
  } catch (err) {
    return '';
  }
}

function logApiUse_(feature, config, estimatedTokens, status, errorMessage, usageMetadata) {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, SHEETS.API_LOG, HEADERS.API_LOG);
  ensureHeaderIncludes_(sheet, HEADERS.API_LOG);

  const now = new Date();
  const usage = usageMetadata || {};
  const valuesByHeader = {
    '시간': now,
    '날짜': Utilities.formatDate(now, Session.getScriptTimeZone(), 'yyyy-MM-dd'),
    '기능': feature,
    '프로젝트명': config.projectName,
    '모델명': normalizeModelName_(config.model || DEFAULT_MODEL),
    'API키끝4자리': config.apiKey.slice(-4),
    '요청수': 1,
    '예상입력토큰': estimatedTokens,
    '실제입력토큰': getUsageTokenCount_(usage, 'promptTokenCount'),
    '출력토큰': getUsageTokenCount_(usage, 'candidatesTokenCount'),
    '사고토큰': getUsageTokenCount_(usage, 'thoughtsTokenCount'),
    '캐시토큰': getUsageTokenCount_(usage, 'cachedContentTokenCount'),
    '총토큰': getUsageTokenCount_(usage, 'totalTokenCount'),
    '상태': status,
    '오류메시지': errorMessage || ''
  };
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const row = headers.map(header => {
    const key = String(header || '').trim();
    return Object.prototype.hasOwnProperty.call(valuesByHeader, key) ? valuesByHeader[key] : '';
  });
  sheet.getRange(sheet.getLastRow() + 1, 1, 1, row.length).setValues([row]);
}

function getUsageTokenCount_(usage, key) {
  if (!usage) return 0;
  if (usage[key] !== undefined && usage[key] !== null) return Number(usage[key] || 0);

  const snakeKey = key.replace(/[A-Z]/g, letter => '_' + letter.toLowerCase());
  if (usage[snakeKey] !== undefined && usage[snakeKey] !== null) return Number(usage[snakeKey] || 0);

  return 0;
}

function getGlobalBatchSize_() {
  const configs = readAdminConfigs_();
  if (!configs.length) return DEFAULT_BATCH_SIZE;
  return Math.max(1, Math.min(10, Number(configs[0].batchSize || DEFAULT_BATCH_SIZE)));
}

function estimateTokens_(text) {
  return Math.ceil(String(text || '').length / 4);
}

function estimateRequestTokens_(prompt, extraParts, keyConfig) {
  const attachmentCount = (extraParts || []).length;
  const attachmentBudget = Number((keyConfig || {}).attachmentTokenBudget || 0);
  const outputBudget = Number((keyConfig || {}).outputTokenBudget || 0);
  return estimateTokens_(prompt) + attachmentCount * attachmentBudget + outputBudget;
}

function defaultAttachmentTokenBudget_(feature) {
  return feature === TASK_TYPES.PROBLEM_ANALYSIS ? 15000 : 0;
}

function defaultOutputTokenBudget_(feature) {
  if (feature === TASK_TYPES.PROBLEM_ANALYSIS) return 3000;
  if (feature === TASK_TYPES.STUDENT_REPORT) return 5000;
  if (feature === TASK_TYPES.SIMILAR_PROBLEMS) return 9000;
  return 0;
}

function pickRoundRobinConfig_(feature, sheetScope, configs) {
  const key = buildRoundRobinPropertyKey_(feature, sheetScope);
  const props = PropertiesService.getScriptProperties();
  const lastProjectName = props.getProperty(key);
  let nextIndex = 0;

  if (lastProjectName) {
    const currentIndex = configs.findIndex(config => config.projectName === lastProjectName);
    nextIndex = currentIndex >= 0 ? (currentIndex + 1) % configs.length : 0;
  }

  const selected = configs[nextIndex];
  props.setProperty(key, selected.projectName);
  return selected;
}

function buildRoundRobinPropertyKey_(feature, sheetScope) {
  return 'ROUND_ROBIN__' + String(feature || '') + '__' + String(sheetScope || '*');
}

function throwDefer_(message, deferMs) {
  const error = new Error(message);
  error.deferOnly = true;
  error.deferMs = deferMs || 60 * 1000;
  throw error;
}

function isTemporaryHttpError_(statusCode) {
  return [429, 500, 502, 503, 504].indexOf(Number(statusCode)) >= 0;
}

function normalizeModelName_(model) {
  return String(model || DEFAULT_MODEL).replace(/^models\//, '').trim();
}

function matchesSheetScope_(configuredScope, currentSheetName) {
  const scope = String(configuredScope || '').trim();
  if (!scope || scope === '*') return true;
  if (!currentSheetName) return true;
  const aliases = getSheetScopeAliases_(currentSheetName);
  return scope.split(',').map(item => item.trim()).some(item => aliases[item]);
}

function getOrCreateChildFolder_(parent, name) {
  const iterator = parent.getFoldersByName(name);
  if (iterator.hasNext()) return iterator.next();
  return parent.createFolder(name);
}

function distributeCounts_(total, labels) {
  const result = {};
  labels.forEach(label => result[label] = 0);
  if (!labels.length) return result;

  const base = Math.floor(total / labels.length);
  labels.forEach(label => result[label] = base);
  let remainder = total - base * labels.length;
  const shuffled = shuffle_(labels.slice());
  for (let i = 0; i < remainder; i++) {
    result[shuffled[i % shuffled.length]] += 1;
  }
  return result;
}

function splitEvenly_(items, bucketCount) {
  const buckets = [];
  const count = Math.max(1, bucketCount);
  for (let i = 0; i < count; i++) buckets.push([]);
  items.forEach((item, index) => buckets[index % count].push(item));
  return buckets;
}

function chunkByMaxSize_(items, maxSize) {
  const chunks = [];
  const size = Math.max(1, maxSize);
  for (let i = 0; i < items.length; i += size) {
    chunks.push(items.slice(i, i + size));
  }
  return chunks;
}

function shuffle_(items) {
  const copy = items.slice();
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    const temp = copy[i];
    copy[i] = copy[j];
    copy[j] = temp;
  }
  return copy;
}

function randomInt_(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function randomChoice_(items) {
  return items[Math.floor(Math.random() * items.length)];
}

function seedAdminExamples_(sheet) {
  if (sheet.getLastRow() > 1) return;
  sheet.getRange(2, 1, 5, HEADERS.ADMIN.length).setValues([
    [TASK_TYPES.PROBLEM_ANALYSIS, '*', 'A-project-01', '여기에_API_KEY', 10, 250000, 250, DEFAULT_MODEL, 2, 12000, 15000, 3000, '여기에_DRIVE_ROOT_FOLDER_ID', 0, 0, 'FALSE'],
    [TASK_TYPES.PROBLEM_ANALYSIS, '*', 'A-project-02', '여기에_API_KEY', 10, 250000, 250, DEFAULT_MODEL, 2, 12000, 15000, 3000, '여기에_DRIVE_ROOT_FOLDER_ID', 0, 0, 'FALSE'],
    [TASK_TYPES.STUDENT_REPORT, '원장님', 'B-project-01', '여기에_API_KEY', 10, 250000, 250, DEFAULT_MODEL, 2, 12000, 0, 5000, '여기에_DRIVE_ROOT_FOLDER_ID', 0, 0, 'FALSE'],
    [TASK_TYPES.SIMILAR_PROBLEMS, '원장님', 'C-project-01', '여기에_API_KEY', 10, 250000, 250, DEFAULT_MODEL, 2, 12000, 0, 9000, '여기에_DRIVE_ROOT_FOLDER_ID', 24, 6, 'FALSE'],
    [TASK_TYPES.SIMILAR_PROBLEMS, '원장님', 'C-project-02', '여기에_API_KEY', 10, 250000, 250, DEFAULT_MODEL, 2, 12000, 0, 9000, '여기에_DRIVE_ROOT_FOLDER_ID', 24, 6, 'FALSE']
  ]);
}

function seedTwinRuleExamples_(sheet) {
  if (sheet.getLastRow() > 1) return;
  sheet.getRange(2, 1, 2, HEADERS.TWIN_RULES.length).setValues([
    ['이차방정식의 근의 개수', 3, '중', '판별식을 사용해 근의 개수를 판단하는 문제를 만든다.', '원문 숫자와 완전히 같은 계수 사용 금지', 'TRUE', 'TRUE'],
    ['함수의 그래프 해석', 3, '중', '그래프의 교점, 증가/감소, y절편을 묻는 문제를 만든다.', '그림 없이 풀 수 없는 문항 금지', 'TRUE', 'TRUE']
  ]);
}

function protectAndHideAdminSheets_(ss) {
  [SHEETS.ADMIN, SHEETS.API_LOG].forEach(name => {
    const sheet = ss.getSheetByName(name);
    if (!sheet) return;
    try {
      sheet.hideSheet();
      const protection = sheet.protect();
      protection.setDescription(name + ' 보호');
      protection.setWarningOnly(true);
    } catch (err) {
      // Some account types cannot change protection; setup should still finish.
    }
  });
}

function sanitizeFileName_(name) {
  return String(name || '')
    .replace(/[\\/:*?"<>|]/g, '_')
    .trim();
}

function unique_(values) {
  const seen = {};
  return values.filter(value => {
    if (!value || seen[value]) return false;
    seen[value] = true;
    return true;
  });
}

function sum_(values) {
  return values.reduce((acc, value) => acc + Number(value || 0), 0);
}

function clamp_(value, min, max) {
  return Math.max(min, Math.min(max, Number(value || min)));
}
