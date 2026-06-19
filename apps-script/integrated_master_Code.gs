/**
 * Google Sheets Apps Script for math test analysis, student reports,
 * and similar-problem generation with queue-based Gemini API throttling.
 *
 * Install:
 * 1. Open Extensions > Apps Script in the target spreadsheet.
 * 2. Paste this entire file into Code.gs.
 * 3. Run setupSheets() once.
 * 4. Fill 관리자_설정 with API keys and Drive root folder ID.
 * 5. Run installFreeQueueTrigger() and installPaidGenerationQueueTriggers()
 *    separately, or run installQueueTrigger() to install both.
 */

const SHEETS = {
  PROBLEM_BANK: '문제은행',
  QUEUE: '작업큐',
  GENERATION_QUEUE: '문항생성큐',
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

const PERFECT_SCORE_MARKER = '틀린거 없음(100점)';

const QUEUE_STATUS = {
  PENDING: 'PENDING',
  RUNNING: 'RUNNING',
  DONE: 'DONE',
  FAILED: 'FAILED'
};

const RESERVED_SHEETS = [
  SHEETS.PROBLEM_BANK,
  SHEETS.QUEUE,
  SHEETS.GENERATION_QUEUE,
  SHEETS.ADMIN,
  SHEETS.TWIN_RULES,
  SHEETS.API_LOG,
  SHEETS.WRONG_HISTORY,
  SHEETS.WEAKNESS_SUMMARY,
  SHEETS.EXAM_LIST,
  SHEETS.TYPE_MAPPING
];

const HEADERS = {
  PROBLEM_BANK: ['시험지 이름', '문제번호', '링크', '상위 단원', '하위 단원', '문제 유형', '표준 문제 유형', '문항형식', '문제본문', '정답', '풀이요약', '이미지포함여부', '이미지설명', '이미지템플릿', '이미지필수항목', '이미지템플릿근거', '신뢰도', '검산메모', '처리상태', '오류메시지', '마지막처리시간'],
  TEACHER: ['학생 이름', '시험지 이름', '틀린 문제 번호', '분석 보고서', '쌍둥이 문항', '누적 분석 보고서', '처리상태', '오류메시지'],
  QUEUE: ['작업ID', '작업종류', '대상시트', '대상행', '상태', '재시도횟수', '예약시각', '오류메시지', '생성시간', '처리시간', '페이로드JSON'],
  ADMIN: ['기능', '적용시트', '프로젝트명', 'API키', 'RPM', 'TPM', 'RPD', '모델명', '1회처리개수', '요청간대기ms', '첨부토큰보정값', '출력토큰보정값', 'Drive루트폴더ID', '사용여부'],
  TWIN_RULES: ['문제 유형', '기본문항수', '난이도', '생성규칙', '금지사항', '이미지템플릿', '이미지필수항목', '풀이포함여부', '사용여부'],
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
const MAX_RETRIES = 3;
const STALE_RUNNING_MS = 10 * 60 * 1000;

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
    .addItem('무료 작업큐 트리거 설치', 'installFreeQueueTrigger')
    .addItem('무료 작업큐 트리거 삭제', 'removeFreeQueueTriggers')
    .addItem('유료 문항생성큐 트리거 설치', 'installPaidGenerationQueueTriggers')
    .addItem('유료 문항생성큐 트리거 삭제', 'removePaidGenerationQueueTriggers')
    .addItem('무료 작업큐 트리거 설치', 'installFreeQueueTrigger')
    .addItem('무료 작업큐 트리거 삭제', 'removeFreeQueueTriggers')
    .addItem('유료 문항생성큐 트리거 설치', 'installPaidGenerationQueueTriggers')
    .addItem('유료 문항생성큐 트리거 삭제', 'removePaidGenerationQueueTriggers')
    .addItem('무료+유료 트리거 전체 설치', 'installQueueTrigger')
    .addItem('무료+유료 트리거 전체 삭제', 'removeQueueTriggers')
    .addToUi();
}

function setupSheets() {
  const ss = SpreadsheetApp.getActive();
  ensureSheet_(ss, SHEETS.PROBLEM_BANK, HEADERS.PROBLEM_BANK);
  ensureSheet_(ss, SHEETS.QUEUE, HEADERS.QUEUE);
  ensureSheet_(ss, SHEETS.GENERATION_QUEUE, HEADERS.QUEUE);
  ensureSheet_(ss, SHEETS.ADMIN, HEADERS.ADMIN);
  ensureSheet_(ss, SHEETS.TWIN_RULES, HEADERS.TWIN_RULES);
  ensureSheet_(ss, SHEETS.API_LOG, HEADERS.API_LOG);
  ensureSheet_(ss, SHEETS.WRONG_HISTORY, HEADERS.WRONG_HISTORY);
  ensureSheet_(ss, SHEETS.WEAKNESS_SUMMARY, HEADERS.WEAKNESS_SUMMARY);
  ensureSheet_(ss, SHEETS.EXAM_LIST, HEADERS.EXAM_LIST);
  ensureSheet_(ss, SHEETS.TYPE_MAPPING, HEADERS.TYPE_MAPPING);
  refreshExamList();
  refreshTwinRuleDrafts();
  seedAdminExamples_(ss.getSheetByName(SHEETS.ADMIN));
  seedTwinImageCountDefaults_(ss.getSheetByName(SHEETS.ADMIN));
  seedTwinRuleExamples_(ss.getSheetByName(SHEETS.TWIN_RULES));
  protectAndHideAdminSheets_(ss);
  SpreadsheetApp.getUi().alert('초기 시트 생성/정비가 완료되었습니다. 관리자_설정에 API 키와 Drive 루트 폴더 ID를 입력하세요.');
}

function installQueueTrigger() {
  removeFreeQueueTriggers(false);
  removePaidGenerationQueueTriggers(false);
  installFreeQueueTrigger(false);
  installPaidGenerationQueueTriggers(false);
  SpreadsheetApp.getUi().alert(
    '무료 작업큐 트리거와 유료 문항생성큐 병렬 트리거를 모두 설치했습니다.'
  );
}

function installFreeQueueTrigger(showAlert) {
  removeFreeQueueTriggers(false);
  ScriptApp.newTrigger('processQueue')
    .timeBased()
    .everyMinutes(1)
    .create();
  if (showAlert !== false) {
    SpreadsheetApp.getUi().alert('무료 작업큐 트리거 1개를 설치했습니다.');
  }
}

function installPaidGenerationQueueTriggers(showAlert) {
  removePaidGenerationQueueTriggers(false);
  const workerCount = 6;
  for (let index = 0; index < workerCount; index += 1) {
    ScriptApp.newTrigger('processGenerationQueue')
      .timeBased()
      .everyMinutes(1)
      .create();
  }
  if (showAlert !== false) {
    SpreadsheetApp.getUi().alert('유료 문항생성큐 병렬 트리거 ' + workerCount + '개를 설치했습니다.');
  }
}

function removeQueueTriggers() {
  removeFreeQueueTriggers(false);
  removePaidGenerationQueueTriggers(false);
  SpreadsheetApp.getUi().alert('무료 작업큐 트리거와 유료 문항생성큐 트리거를 모두 삭제했습니다.');
}

function removeFreeQueueTriggers(showAlert) {
  removeQueueTriggersByHandlers_(['processQueue']);
  if (showAlert !== false) {
    SpreadsheetApp.getUi().alert('무료 작업큐 트리거를 삭제했습니다.');
  }
}

function removePaidGenerationQueueTriggers(showAlert) {
  removeQueueTriggersByHandlers_(['processGenerationQueue']);
  if (showAlert !== false) {
    SpreadsheetApp.getUi().alert('유료 문항생성큐 트리거를 삭제했습니다.');
  }
}

function removeQueueTriggersByHandlers_(handlerNames) {
  const handlerSet = {};
  handlerNames.forEach(name => handlerSet[name] = true);
  ScriptApp.getProjectTriggers()
    .filter(trigger => handlerSet[trigger.getHandlerFunction()])
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

function refreshTwinRuleDrafts() {
  const ss = SpreadsheetApp.getActive();
  const ruleSheet = ensureSheet_(ss, SHEETS.TWIN_RULES, HEADERS.TWIN_RULES);
  const mappingSheet = ensureSheet_(ss, SHEETS.TYPE_MAPPING, HEADERS.TYPE_MAPPING);

  const existingRules = {};
  readObjects_(ruleSheet).forEach(item => {
    const type = String(item.rowObject['문제 유형'] || '').trim();
    if (type) existingRules[type] = item.rowObject;
  });

  const types = unique_(
    readObjects_(mappingSheet)
      .map(item => item.rowObject)
      .filter(row => String(row['사용여부'] || 'TRUE').toUpperCase() !== 'FALSE')
      .map(row => String(row['표준 문제 유형'] || '').trim())
      .filter(Boolean)
  ).sort();

  if (!types.length) {
    throw new Error('유형매핑 시트에 사용 가능한 표준 문제 유형이 없습니다. 유형매핑 초안을 먼저 갱신하세요.');
  }

  let addedCount = 0;
  const rebuiltRows = types.map(type => {
    const existing = existingRules[type];
    if (existing) {
      const templateHint = findExistingImageTemplate_(type);
      return HEADERS.TWIN_RULES.map(header => {
        if (header === '이미지템플릿' && !String(existing[header] || '').trim()) {
          return templateHint ? templateHint.template : '';
        }
        if (header === '이미지필수항목' && !String(existing[header] || '').trim()) {
          return templateHint ? templateHint.requiredFields : '';
        }
        return existing[header] === undefined ? '' : existing[header];
      });
    }
    addedCount += 1;
    const templateHint = findExistingImageTemplate_(type);
    return [
      type,
      3,
      '중',
      type + ' 유형의 핵심 개념과 풀이 전략을 유지하되 숫자, 조건, 맥락을 바꾼 유사 문항을 생성한다.',
      '원문 문제의 숫자, 조건, 문장 구조를 그대로 복제하지 않는다.',
      templateHint ? templateHint.template : '',
      templateHint ? templateHint.requiredFields : '',
      'TRUE',
      'TRUE'
    ];
  });

  clearSheetBody_(ruleSheet);
  ruleSheet.getRange(2, 1, rebuiltRows.length, HEADERS.TWIN_RULES.length).setValues(rebuiltRows);
  SpreadsheetApp.getUi().alert(
    '유형매핑 기준으로 쌍둥이규칙을 갱신했습니다.\n'
    + '전체 ' + rebuiltRows.length + '개 / 새 초안 ' + addedCount + '개'
  );
  return addedCount;
}

function refreshTypeMappingDrafts() {
  const ss = SpreadsheetApp.getActive();
  const problemSheet = ss.getSheetByName(SHEETS.PROBLEM_BANK);
  const mappingSheet = ensureSheet_(ss, SHEETS.TYPE_MAPPING, HEADERS.TYPE_MAPPING);
  ensureTypeMappingFilter_(mappingSheet);
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
  ensureTypeMappingFilter_(mappingSheet);
  SpreadsheetApp.getUi().alert(newRows.length + '개의 유형매핑 초안을 추가했습니다.');
  return newRows.length;
}

function ensureTypeMappingFilter_(sheet) {
  sheet.setFrozenRows(1);
  const rowCount = Math.max(sheet.getLastRow(), 2);
  const columnCount = Math.max(sheet.getLastColumn(), HEADERS.TYPE_MAPPING.length);
  const existingFilter = sheet.getFilter();
  if (existingFilter) {
    const range = existingFilter.getRange();
    if (range.getLastRow() >= rowCount && range.getLastColumn() >= columnCount) return;

    const criteria = {};
    for (let column = 1; column <= range.getLastColumn(); column += 1) {
      const criterion = existingFilter.getColumnFilterCriteria(column);
      if (criterion) criteria[column] = criterion;
    }
    existingFilter.remove();
    const expandedFilter = sheet.getRange(1, 1, rowCount, columnCount).createFilter();
    Object.keys(criteria).forEach(column => {
      expandedFilter.setColumnFilterCriteria(Number(column), criteria[column]);
    });
    return;
  }

  sheet.getRange(1, 1, rowCount, columnCount).createFilter();
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

  SpreadsheetApp.getUi().alert(updated + '개 문제행에 유형매핑을 적용했습니다.');
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
    const imageFlag = String(item.rowObject['이미지포함여부'] || '').trim().toUpperCase();
    const savedTemplate = String(item.rowObject['이미지템플릿'] || '').trim();
    const savedImageSource = [
      item.rowObject['문제본문'],
      item.rowObject['이미지설명']
    ].join(' ');
    const savedTemplateError = savedTemplate
      ? getImageTemplateSourceCompatibilityError_(savedTemplate, savedImageSource)
      : '';
    const structureAnalyzed = imageFlag === 'FALSE'
      || (imageFlag === 'TRUE'
          && Boolean(String(item.rowObject['이미지설명'] || '').trim())
          && Boolean(savedTemplate)
          && !savedTemplateError);
    if (item.rowObject['문제 유형'] && item.rowObject['정답'] && structureAnalyzed) return;

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
  processQueueSheet_(SHEETS.QUEUE);
}

function processGenerationQueue() {
  processQueueSheet_(SHEETS.GENERATION_QUEUE);
}

function processQueueSheet_(queueSheetName) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;
  let queueSheet = null;
  let claimedItem = null;

  try {
    const ss = SpreadsheetApp.getActive();
    queueSheet = ss.getSheetByName(queueSheetName);
    if (!queueSheet) throw new Error(queueSheetName + ' 시트가 없습니다.');

    recoverStaleRunningTasks_(queueSheet, new Date());
    const queue = readObjects_(queueSheet);
    const now = new Date();
    const runnable = selectRunnableQueueItems_(queue, now, 1);
    claimedItem = runnable.length ? runnable[0] : null;
    if (claimedItem) {
      setRowValues_(queueSheet, claimedItem.rowNumber, getHeaderMap_(queueSheet), {
        '상태': QUEUE_STATUS.RUNNING,
        '처리시간': now,
        '오류메시지': ''
      });
      claimedItem.rowObject['상태'] = QUEUE_STATUS.RUNNING;
      claimedItem.rowObject['처리시간'] = now;
    }
  } finally {
    lock.releaseLock();
  }

  if (claimedItem) {
    processQueueItem_(queueSheet, claimedItem);
  }
}

function isPaidGenerationTask_(taskType) {
  return taskType === TASK_TYPES.SIMILAR_PROBLEMS
    || taskType === TASK_TYPES.GENERAL_PROBLEMS
    || taskType === TASK_TYPES.PAST_EXAM_PROBLEMS;
}

function recoverStaleQueueTasks() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return 0;

  try {
    const ss = SpreadsheetApp.getActive();
    const queueSheet = ss.getSheetByName(SHEETS.QUEUE);
    if (!queueSheet) throw new Error('작업큐 시트가 없습니다.');
    return recoverStaleRunningTasks_(queueSheet, new Date());
  } finally {
    lock.releaseLock();
  }
}

function recoverStaleRunningTasks_(queueSheet, now) {
  const queueHeaders = getHeaderMap_(queueSheet);
  let recovered = 0;

  readObjects_(queueSheet).forEach(item => {
    if (item.rowObject['상태'] !== QUEUE_STATUS.RUNNING) return;

    const startedAt = item.rowObject['처리시간'];
    const startedMs = startedAt ? new Date(startedAt).getTime() : 0;
    if (startedMs && now.getTime() - startedMs < STALE_RUNNING_MS) return;

    const nextRetryCount = Number(item.rowObject['재시도횟수'] || 0) + 1;
    const nextStatus = nextRetryCount >= MAX_RETRIES
      ? QUEUE_STATUS.FAILED
      : QUEUE_STATUS.PENDING;

    setRowValues_(queueSheet, item.rowNumber, queueHeaders, {
      '상태': nextStatus,
      '재시도횟수': nextRetryCount,
      '예약시각': nextStatus === QUEUE_STATUS.PENDING
        ? new Date(now.getTime() + 60 * 1000)
        : '',
      '오류메시지': '이전 실행이 제한시간 초과 또는 강제 종료되어 자동 복구되었습니다.',
      '처리시간': now
    });
    recovered += 1;
  });

  return recovered;
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
    '각 문제에 대해 반드시 문제를 끝까지 풀고, 정답을 검산한 뒤 문제본문, 문제 유형, 상위 단원, 하위 단원, 문항형식, 정답을 작성하라.',
    '정답을 추측하지 말라. 풀이 근거가 부족하거나 이미지 판독이 불확실하면 confidence를 MEDIUM 또는 LOW로 낮추고 reviewReason에 이유를 적어라.',
    '도형, 그래프, 길이, 넓이, 각도, 단위(cm, m 등), 분수/무리수 답은 특히 조건을 다시 확인하고 검산하라.',
    '문제에서 요구하는 단위와 답 형식에 맞게 최종 정답을 정리하라.',
    'solutionSummary에는 핵심 풀이와 검산 근거를 1~2문장으로 적어라.',
    'solutionSummary는 구글 시트 셀에서 바로 읽을 수 있는 평문으로 작성하라. LaTeX, 마크다운, $...$, \\( ... \\), \\frac, \\sqrt 같은 표기를 쓰지 말고 x², √3, 3/4처럼 일반 텍스트와 유니코드 기호로 적어라.',
    'confidence는 HIGH, MEDIUM, LOW 중 하나만 사용하라.',
    'reviewReason은 사람이 확인해야 할 이유가 있을 때만 적고, 확실하면 빈 문자열로 둔다.',
    'problemText에는 보기, 조건, 소문항을 포함한 원문 문제를 평문으로 정확히 옮겨라.',
    'formType은 원문 문항형식에 맞춰 5지선다형, 단답형, 서술형 중 하나만 작성하라. ①~⑤ 선택지가 있으면 5지선다형, 풀이과정·이유 서술을 요구하면 서술형, 그 외 직접 답만 쓰는 문제는 단답형이다.',
    '도형, 그래프, 좌표평면, 표가 있으면 hasImage를 true로 하고 imageDescription에 구조를 상세히 적어라.',
    'imageDescription에는 함수 식, 점의 위치와 관계, 선분 연결, 도형 종류, 축과의 관계를 포함하되 템플릿 이름은 추측하지 마라.',
    '이미지가 없으면 hasImage는 false, imageDescription은 빈 문자열로 작성하라.',
    '반드시 JSON 배열만 반환하라. 설명, 마크다운, 코드블록은 금지.',
    '형식: [{"problemNumber":"1","problemText":"문제 원문","type":"삼각형의 닮음과 길이","unit1":"도형","unit2":"닮음","formType":"단답형","answer":"9cm","solutionSummary":"닮음비를 이용해 대응변의 길이를 구하고 단위를 확인하면 9cm이다.","hasImage":true,"imageDescription":"삼각형 ABC와 내부 선분 DE의 점·선·길이 관계","confidence":"HIGH","reviewReason":""}]',
    '문제를 찾을 수 없으면 problemText, type, unit1, unit2, formType, answer, solutionSummary, imageDescription을 빈 문자열로 두고 hasImage는 false, confidence는 LOW, reviewReason은 "문제를 찾지 못함"으로 둔다.',
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
    const problemText = String(result.problemText || result.problem || '').trim();
    const hasImage = result.hasImage === true || String(result.hasImage || '').toUpperCase() === 'TRUE';
    const imageDescription = String(result.imageDescription || '').trim();
    const imageSourceText = [
      result.unit1,
      result.unit2,
      result.type,
      problemText,
      imageDescription
    ].join(' ');
    const imageTemplateHint = hasImage
      ? findExistingImageTemplate_(imageSourceText)
      : null;
    const templateCompatibilityError = imageTemplateHint
      ? getImageTemplateSourceCompatibilityError_(imageTemplateHint.template, imageSourceText)
      : '';
    const templateReviewReason = hasImage && !imageTemplateHint
      ? '원문 이미지 구조와 일치하는 렌더러 템플릿을 찾지 못했습니다.'
      : templateCompatibilityError;
    const hasResult = Boolean(result.type || result.answer);
    const needsReview = hasResult && (confidence !== 'HIGH' || reviewReason || templateReviewReason);
    const updates = {
      '처리상태': hasResult ? (needsReview ? 'REVIEW' : 'DONE') : 'NO_RESULT',
      '오류메시지': hasResult ? '' : 'AI 응답에서 해당 문제번호를 찾지 못했습니다.',
      '마지막처리시간': new Date()
    };
    if (result.type) updates['문제 유형'] = result.type;
    if (result.unit1) updates['상위 단원'] = result.unit1;
    if (result.unit2) updates['하위 단원'] = result.unit2;
    if (problemText) updates['문제본문'] = problemText;
    if (result.type) updates['표준 문제 유형'] = getStandardType_(result.type, result.unit1, result.unit2);
    updates['문항형식'] = normalizeProblemFormType_(result.formType) || inferProblemFormType_(problemText);
    if (result.answer) updates['정답'] = result.answer;
    if (result.solutionSummary) updates['풀이요약'] = result.solutionSummary;
    updates['이미지포함여부'] = hasImage ? 'TRUE' : 'FALSE';
    updates['이미지설명'] = hasImage ? imageDescription : '';
    updates['이미지템플릿'] = imageTemplateHint && !templateCompatibilityError
      ? imageTemplateHint.template
      : '';
    updates['이미지필수항목'] = imageTemplateHint && !templateCompatibilityError
      ? imageTemplateHint.requiredFields
      : '';
    updates['이미지템플릿근거'] = imageTemplateHint
      ? buildImageTemplateEvidence_(imageTemplateHint.template, imageSourceText)
      : '';
    if (confidence) updates['신뢰도'] = confidence;
    updates['검산메모'] = [reviewReason, templateReviewReason].filter(Boolean).join(' / ')
      || (needsReview ? '신뢰도 ' + confidence + '로 사람 확인이 필요합니다.' : '');
    setRowValues_(sheet, row.rowNumber, headers, updates);
  });
}

function handleStudentReport_(targetSheetName, targetRow, payload) {
  const ss = SpreadsheetApp.getActive();
  const teacherSheet = ss.getSheetByName(targetSheetName);
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
  const teacherSheet = ss.getSheetByName(targetSheetName);
  const teacherHeaders = getHeaderMap_(teacherSheet);
  const rowObject = readRowObject_(teacherSheet, targetRow);
  const actualWrongProblems = lookupWrongProblems_(payload.examName, payload.wrongNumbersText);
  if (!actualWrongProblems.length) {
    throw new Error('틀린 문제가 없는 100점 기록에는 쌍둥이문항을 생성하지 않습니다.');
  }
  const twinSourceProblems = expandTwinSourceProblems_(payload.examName, actualWrongProblems);
  const rulesByType = readTwinRules_();
  const missingTypes = unique_(twinSourceProblems.map(item => item.type).filter(type => !rulesByType[type]));
  if (missingTypes.length) {
    throw new Error('쌍둥이_규칙 시트에 규칙이 없는 문제 유형: ' + missingTypes.join(', '));
  }

  const reportText = readDriveTextFromUrl_(rowObject['분석 보고서']);
  const plan = buildTwinGenerationPlan_(twinSourceProblems, targetSheetName, rulesByType);
  const generatedProblems = generateSimilarProblemsWithPool_(
    targetSheetName,
    payload.studentName,
    payload.examName,
    twinSourceProblems,
    reportText,
    rulesByType,
    plan
  );
  const finalText = formatGeneratedProblems_(payload.studentName, payload.examName, plan, generatedProblems);
  const fileUrl = saveTextToStudentFolder_(
    payload.studentName,
    sanitizeFileName_(payload.studentName + '_' + payload.examName + '_쌍둥이문항.txt'),
    finalText
  );
  upsertWrongHistory_(targetSheetName, targetRow, payload.studentName, payload.examName, actualWrongProblems, {
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
      throwDefer_(message, getTemporaryRetryDelayMs_(feature, statusCode, raw));
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
  let temporaryRetryMs = 0;
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
        temporaryRetryMs = Math.max(
          temporaryRetryMs,
          getTemporaryRetryDelayMs_(feature, statusCode, raw)
        );
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
    results.push({
      text,
      raw: json,
      usageMetadata: json.usageMetadata || {},
      requestIndex: index
    });
  });

  if (temporaryErrorMessage) {
    const error = new Error(temporaryErrorMessage);
    error.deferOnly = true;
    error.deferMs = temporaryRetryMs || 15 * 60 * 1000;
    error.partialResults = results;
    throw error;
  }
  if (fatalErrorMessage) {
    const error = new Error(fatalErrorMessage);
    error.partialResults = results;
    throw error;
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

function getTemporaryRetryDelayMs_(feature, statusCode, rawErrorText) {
  if (!isPaidGenerationTask_(feature)) return 15 * 60 * 1000;

  if (Number(statusCode) === 429) {
    const retrySeconds = extractRetrySeconds_(rawErrorText);
    return retrySeconds
      ? Math.max(15 * 1000, Math.ceil((retrySeconds + 5) * 1000))
      : 60 * 1000;
  }
  if ([502, 503, 504].indexOf(Number(statusCode)) >= 0) return 45 * 1000;
  if (Number(statusCode) === 500) return 60 * 1000;
  return 60 * 1000;
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
  const perfectScore = !wrongProblems.length;
  return [
    '너는 중고등학교 수학학원에서 학부모 상담용 보고서를 작성하는 교사다.',
    '문체는 친절하고 구체적이되 과장하지 말라.',
    '다음 순서로 텍스트 보고서를 작성하라.',
    '1. 전체 요약',
    '2. 주요 약점 유형',
    '3. 유형별 해설과 원인 추정',
    '4. 누적 기록 기준의 개선/반복 약점',
    '5. 다음 주 학습 방향',
    '6. 가정에서 확인할 과제 제안',
    '누적 기록이 충분하지 않으면 이번 시험 기준 분석이라고 명시하라.',
    '이번 시험 결과가 100점이면 약점이 있다고 억지로 추정하지 말고 성취를 명확히 기록한 뒤, 누적 기록에 약점이 있을 때만 유지 학습 방향을 간단히 제안하라.',
    '오답이 있으면 이번 시험의 오답과 누적 기록을 함께 분석하라.',
    '',
    '마크다운 표는 쓰지 말고 일반 txt 문서처럼 작성하라.',
    '',
    '이번 요청 데이터:',
    '학생명: ' + studentName,
    '시험명: ' + examName,
    '오답 목록(JSON): ' + JSON.stringify(wrongProblems),
    '이번 시험 결과: ' + (perfectScore ? PERFECT_SCORE_MARKER : '오답 있음'),
    '누적 오답 요약(JSON): ' + JSON.stringify(historySummary || {})
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

  const chunks = chunkTwinPlanItems_(plan.items, 3);

  const requests = chunks.map((chunk, index) => {
    const keyConfig = availableKeys[index % availableKeys.length];
    const prompt = buildSimilarProblemsPrompt_(studentName, examName, wrongProblems, reportText, rulesByType, chunk);
    if (!isWithinQuota_(keyConfig, estimateRequestTokens_(prompt, [], keyConfig))) {
      throwDefer_(keyConfig.projectName + ' 프로젝트 quota가 부족하여 다음 트리거로 연기합니다.');
    }
    return { keyConfig, prompt, extraParts: [], planItems: chunk };
  });

  let generated = [];
  let responses;
  try {
    responses = callGeminiBatch_(TASK_TYPES.SIMILAR_PROBLEMS, requests);
  } catch (error) {
    error.partialGeneratedProblems = parsePartialSimilarProblemResponses_(
      error.partialResults,
      requests
    );
    throw error;
  }
  generated = parseSimilarProblemResponsesSafely_(responses, requests);
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
  for (let attempt = 0; attempt < 3; attempt++) {
    const issues = findGeneratedProblemIssues_(plan, current);
    if (!issues.retryNumbers.length) return current;

    const retrySet = {};
    issues.retryNumbers.forEach(number => retrySet[number] = true);
    const retryItems = plan.items
      .filter(item => retrySet[Number(item.number)])
      .map(item => Object.assign({}, item, {
        retryReason: getGeneratedProblemRetryReason_(Number(item.number), issues)
      }));
    let retryGenerated;
    try {
      retryGenerated = requestSimilarProblemRetries_(
        targetSheetName,
        studentName,
        examName,
        wrongProblems,
        reportText,
        rulesByType,
        retryItems
      );
    } catch (error) {
      const partialRetrySet = {};
      (error.partialGeneratedProblems || []).forEach(item => {
        partialRetrySet[Number(item.number)] = true;
      });
      error.partialGeneratedProblems = mergeGeneratedProblemRetries_(
        current,
        error.partialGeneratedProblems || [],
        partialRetrySet
      );
      throw error;
    }
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
  // Retry each failed item independently. A model omission in one image contract
  // must not invalidate or distract from the other paid generations.
  const chunks = chunkTwinPlanItems_(retryItems, 1);
  if (!availableKeys.length) {
    throwDefer_(targetSheetName + ' 시트의 문제생성기 재시도에 사용 가능한 프로젝트 quota가 부족합니다.');
  }

  const requests = chunks.map((chunk, index) => {
    const keyConfig = availableKeys[index % availableKeys.length];
    const prompt = buildSimilarProblemsPrompt_(
      studentName,
      examName,
      wrongProblems,
      reportText,
      rulesByType,
      chunk
    ) + '\n\n재시도 전용 지시:\n'
      + '- 이전 응답은 아래 검증 사유로 거절되었다. 이전 문장을 부분 수정하지 말고 해당 문항을 처음부터 새로 작성하라.\n'
      + '- 문제 제작 과정, 계산 실패, 숫자나 선택지를 바꾼 과정은 출력하지 말고 최종 확정된 문제와 풀이만 출력하라.\n'
      + '- 이미지 문항은 계획의 imageRequired와 이미지 계약을 정확히 지켜라.\n'
      + buildTwinRetryGuide_(chunk);
    if (!isWithinQuota_(keyConfig, estimateRequestTokens_(prompt, [], keyConfig))) {
      throwDefer_(keyConfig.projectName + ' 프로젝트 quota가 부족하여 다음 트리거로 연기합니다.');
    }
    return { keyConfig, prompt, extraParts: [], planItems: chunk };
  });

  let generated = [];
  let responses;
  try {
    responses = callGeminiBatch_(TASK_TYPES.SIMILAR_PROBLEMS, requests);
  } catch (error) {
    error.partialGeneratedProblems = parsePartialSimilarProblemResponses_(
      error.partialResults,
      requests
    );
    throw error;
  }
  return parseSimilarProblemResponsesSafely_(responses, requests);
}

function getGeneratedProblemRetryReason_(number, issues) {
  if ((issues.missingNumbers || []).indexOf(number) !== -1) {
    return 'AI 응답에서 해당 문항번호가 누락됨';
  }
  if ((issues.incompleteNumbers || []).indexOf(number) !== -1) {
    return '문제, 정답 또는 해설이 누락됨';
  }
  if ((issues.draftLeakNumbers || []).indexOf(number) !== -1) {
    return '문제 수정 과정이나 초안 문장이 포함됨';
  }
  if ((issues.groupedSubproblemNumbers || []).indexOf(number) !== -1) {
    return (issues.groupedSubproblemReasons && issues.groupedSubproblemReasons[number])
      || '묶음 소문항이 일부 누락됨';
  }
  if ((issues.formTypeNumbers || []).indexOf(number) !== -1) {
    return (issues.formTypeReasons && issues.formTypeReasons[number])
      || '생성유형 형식이 문항 계획과 일치하지 않음';
  }
  if ((issues.mathConventionNumbers || []).indexOf(number) !== -1) {
    return (issues.mathConventionReasons && issues.mathConventionReasons[number])
      || '계수 해석 규칙이 문제와 해설에서 일치하지 않음';
  }
  if ((issues.duplicateNumbers || []).indexOf(number) !== -1) {
    return (issues.duplicateReasons && issues.duplicateReasons[number])
      || '다른 생성 문항과 문제 본문이 중복됨';
  }
  if ((issues.imagePromptNumbers || []).indexOf(number) !== -1) {
    return (issues.imagePromptReasons && issues.imagePromptReasons[number])
      || '이미지 계약이 올바르지 않음';
  }
  return '출력 검증 실패';
}

function buildTwinRetryGuide_(planItems) {
  return (planItems || []).map(item => [
    '문항' + item.number + ' 검증 사유: ' + String(item.retryReason || '출력 검증 실패'),
    buildTwinImageContractGuide_([item])
  ].join('\n')).join('\n\n');
}

function buildTwinImageContractGuide_(planItems) {
  return (planItems || []).map(item => {
    if (!item.imageRequired) {
      return [
        '문항' + item.number + ': 이미지 없음. [이미지 필요]와 IMAGE_PROMPT 출력 금지.',
        '그림을 보라고 지시하지 말고 점의 위치, 도형의 종류, 변의 평행·수직 관계와 함수 위의 점 조건을 본문에 모두 적어 독립적으로 풀 수 있게 작성.'
      ].join('\n');
    }
    const fields = String(item.imageRequiredFields || '')
      .split(',')
      .map(field => field.trim())
      .filter(Boolean);
    const lines = [
      '문항' + item.number + ':',
      '[이미지 필요: ' + item.imageTemplate + ']',
      '[IMAGE_PROMPT:',
      'template=' + item.imageTemplate
    ];
    fields.forEach(field => {
      lines.push(field + '=<문제와 정답에 맞는 확정값>');
    });
    lines.push(']');
    return lines.join('\n');
  }).join('\n\n');
}

function findGeneratedProblemIssues_(plan, generated) {
  const generatedByNumber = {};
  generated.forEach(item => {
    generatedByNumber[Number(item.number)] = item;
  });
  const planNumbers = plan.items.map(item => Number(item.number));
  const missingNumbers = planNumbers.filter(number => !generatedByNumber[number]);
  const incompleteNumbers = planNumbers.filter(number => {
    const generatedItem = generatedByNumber[number] || {};
    return !String(generatedItem.problem || generatedItem.body || '').trim()
      || !String(generatedItem.answer || '').trim()
      || !String(generatedItem.solution || '').trim();
  });
  const draftLeakNumbers = planNumbers.filter(number => hasDraftLeakText_(generatedByNumber[number]));
  const planByNumber = {};
  plan.items.forEach(item => {
    planByNumber[Number(item.number)] = item;
  });
  const imagePromptReasons = {};
  const imagePromptNumbers = planNumbers.filter(number => {
    const reason = getGeneratedProblemImageIssue_(
      generatedByNumber[number] || {},
      planByNumber[number] || {}
    );
    if (reason) imagePromptReasons[number] = reason;
    return Boolean(reason);
  });
  const groupedSubproblemReasons = {};
  const groupedSubproblemNumbers = planNumbers.filter(number => {
    const planItem = planByNumber[number] || {};
    const expectedCount = Number(planItem.sourceSubproblemCount || 0);
    if (expectedCount < 2) return false;
    const generatedItem = generatedByNumber[number] || {};
    const problemText = String(generatedItem.problem || generatedItem.body || '');
    const missingMarkers = [];
    for (let index = 1; index <= expectedCount; index++) {
      if (problemText.indexOf('(' + index + ')') === -1) missingMarkers.push('(' + index + ')');
    }
    if (!missingMarkers.length) return false;
    groupedSubproblemReasons[number] = '묶음 소문항 ' + missingMarkers.join(', ') + ' 누락';
    return true;
  });
  const formTypeReasons = {};
  const formTypeNumbers = planNumbers.filter(number => {
    const reason = getGeneratedProblemFormTypeIssue_(
      generatedByNumber[number] || {},
      planByNumber[number] || {}
    );
    if (reason) formTypeReasons[number] = reason;
    return Boolean(reason);
  });
  const mathConventionReasons = {};
  const mathConventionNumbers = planNumbers.filter(number => {
    const reason = getGeneratedProblemMathConventionIssue_(generatedByNumber[number] || {});
    if (reason) mathConventionReasons[number] = reason;
    return Boolean(reason);
  });
  const duplicateReasons = {};
  const duplicateNumbers = [];
  const firstNumberByProblem = {};
  planNumbers.forEach(number => {
    const generatedItem = generatedByNumber[number] || {};
    const normalizedProblem = normalizeGeneratedProblemForDuplicateCheck_(
      generatedItem.problem || generatedItem.body || ''
    );
    if (!normalizedProblem) return;
    if (firstNumberByProblem[normalizedProblem]) {
      duplicateNumbers.push(number);
      duplicateReasons[number] = '문항' + firstNumberByProblem[normalizedProblem]
        + '과 문제 본문·수치가 중복됨';
      return;
    }
    firstNumberByProblem[normalizedProblem] = number;
  });
  return {
    missingNumbers,
    incompleteNumbers,
    draftLeakNumbers,
    imagePromptNumbers,
    imagePromptReasons,
    groupedSubproblemNumbers,
    groupedSubproblemReasons,
    formTypeNumbers,
    formTypeReasons,
    mathConventionNumbers,
    mathConventionReasons,
    duplicateNumbers,
    duplicateReasons,
    retryNumbers: unique_(
      missingNumbers
        .concat(incompleteNumbers)
        .concat(draftLeakNumbers)
        .concat(groupedSubproblemNumbers)
        .concat(formTypeNumbers)
        .concat(mathConventionNumbers)
        .concat(duplicateNumbers)
        .concat(imagePromptNumbers)
    )
  };
}

function normalizeGeneratedProblemForDuplicateCheck_(value) {
  return String(value || '')
    .replace(/\[이미지\s*필요\s*:[\s\S]*?\]/gi, ' ')
    .replace(/\[IMAGE_PROMPT\s*:[\s\S]*?\]/gi, ' ')
    .replace(/문항\s*\d+\s*\./g, ' ')
    .replace(/\[수식\s*:\s*/g, '')
    .replace(/\]/g, '')
    .replace(/\s+/g, '')
    .replace(/[.,。]/g, '')
    .trim();
}

function getGeneratedProblemFormTypeIssue_(generatedItem, planItem) {
  const problemText = String(generatedItem.problem || generatedItem.body || '');
  const formType = String(planItem.formType || '');
  const numberingIssue = getGeneratedNumberingStyleIssue_(problemText);
  if (numberingIssue) return numberingIssue;
  const choiceCounts = ['①', '②', '③', '④', '⑤'].map(marker =>
    (problemText.match(new RegExp(marker, 'g')) || []).length
  );
  const choiceCount = choiceCounts.reduce((sum, count) => sum + count, 0);
  const choiceDiversityIssue = getGeneratedChoiceDiversityIssue_(problemText);
  if (choiceDiversityIssue) return choiceDiversityIssue;
  if (
    formType === '5지선다형'
    && (choiceCount !== 5 || choiceCounts.some(count => count !== 1))
  ) {
    return '5지선다형 문항에 ①~⑤ 선택지가 각각 정확히 한 번씩 있어야 합니다.';
  }
  if (formType !== '5지선다형' && choiceCount > 0) {
    return formType + ' 문항에 5지선다 선택지가 포함되었습니다.';
  }
  if (formType === '5지선다형' && Number(planItem.sourceSubproblemCount || 0) >= 2) {
    const asksOrderedAnswers = /(?:답|정답).*(?:순서|차례)|순서대로.*(?:나열|고른)|옳게\s*짝지어진/.test(problemText);
    if (!asksOrderedAnswers) {
      return '소문항 묶음 5지선다형은 모든 소문항의 답을 순서대로 짝지은 선택지를 고르게 해야 합니다.';
    }
  }
  return '';
}

function getGeneratedChoiceDiversityIssue_(problemText) {
  const choices = String(problemText || '')
    .split(/\n/)
    .map(line => line.trim())
    .filter(line => /^[\u2460-\u2464]/.test(line));
  if (choices.length !== 5) return '';

  const bodies = choices.map(line => line
    .replace(/^[\u2460-\u2464]\s*/, '')
    .replace(/\[수식:\s*/g, '')
    .replace(/\]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
  );
  const uniqueBodies = Array.from(new Set(bodies.map(normalizeChoiceForDiversity_)));
  if (uniqueBodies.length < 5) {
    return '5지선다형 선택지 5개가 서로 달라야 합니다. 같은 선택지가 반복되었습니다.';
  }

  const firstTupleItems = bodies.map(body => {
    const match = body.match(/^\(?\s*([^,，)]+?)\s*[,，]/);
    return match ? normalizeChoiceForDiversity_(match[1]) : '';
  }).filter(Boolean);
  if (
    firstTupleItems.length === 5
    && Array.from(new Set(firstTupleItems)).length === 1
  ) {
    return '5지선다형 선택지 5개가 모두 같은 첫 항목을 반복합니다. 공통값은 본문으로 빼고 변별되는 값만 선택지에 남기세요.';
  }
  return '';
}

function normalizeChoiceForDiversity_(text) {
  return String(text || '')
    .replace(/\s+/g, '')
    .replace(/[()]/g, '')
    .toLowerCase();
}

function getGeneratedProblemMathConventionIssue_(generatedItem) {
  const problemText = String(generatedItem.problem || generatedItem.body || '');
  const solutionText = String(generatedItem.solution || '');
  const match = problemText.match(/([xy])의\s*계수\s*-\s*1을\s*(?:어떤\s*수\s*)?([a-z])로\s*잘못\s*보고/i);
  if (!match) return '';
  const variable = match[1];
  const parameter = match[2];
  const wrongSignedTerm = new RegExp('-\\s*' + escapeRegExp_(parameter) + '\\s*' + variable, 'i');
  if (wrongSignedTerm.test(solutionText)) {
    return '계수 -1을 ' + parameter + '로 잘못 본 경우에는 -'
      + variable + '를 +' + parameter + variable + '로 바꿔야 하며 -'
      + parameter + variable + '로 쓰면 안 됩니다.';
  }
  const squareRoleIssue = getAxisAlignedSquareBodySolutionIssue_(problemText, solutionText);
  if (squareRoleIssue) return squareRoleIssue;
  const decimalChoiceIssue = getDecimalChoiceFormatIssue_(problemText);
  if (decimalChoiceIssue) return decimalChoiceIssue;
  return '';
}

function getDecimalChoiceFormatIssue_(problemText) {
  const text = String(problemText || '');
  if (/삼각비|근삿값|소수|반올림|표/.test(text)) return '';
  const choiceLines = text
    .split(/\n/)
    .map(line => line.trim())
    .filter(line => /^[①②③④⑤]/.test(line));
  const hasDecimalChoice = choiceLines.some(line => /[-+]?\d+\.\d+/.test(line));
  return hasDecimalChoice ? '선택지의 유리수 값은 소수가 아니라 기약분수 꼴로 작성해야 합니다.' : '';
}

function getAxisAlignedSquareBodySolutionIssue_(problemText, solutionText) {
  const problem = normalizeRoleCheckText_(problemText);
  const solution = normalizeRoleCheckText_(solutionText);
  if (problem.indexOf('정사각형ABCD') < 0) return '';

  const marker = problem.match(/점C(?:와|,)?점?D는각각(?:일차함수)?([^,]+?)(?:와|,)([^의]+?)의그래프위/);
  if (!marker) return '';
  const cEquation = String(marker[1] || '').replace(/^일차함수/, '').trim();
  const dEquation = String(marker[2] || '').replace(/^일차함수/, '').trim();
  if (!cEquation || !dEquation) return '';

  const normalizedSolution = solution.replace(/일차함수/g, '');
  const solutionSaysDIsCEquation = normalizedSolution.indexOf('점D는' + cEquation + '위') >= 0;
  const solutionSaysCIsDEquation = normalizedSolution.indexOf('점C는' + dEquation + '위') >= 0;
  if (solutionSaysDIsCEquation || solutionSaysCIsDEquation) {
    return '정사각형 ABCD에서 문제 본문의 C·D 함수 배치와 해설의 C·D 함수 배치가 서로 반대입니다.';
  }
  return '';
}

function parsePartialSimilarProblemResponses_(responses, requests) {
  return parseSimilarProblemResponsesSafely_(responses, requests);
}

function parseSimilarProblemResponsesSafely_(responses, requests) {
  let generated = [];
  (responses || []).forEach((response, index) => {
    const requestIndex = Number.isFinite(response.requestIndex) ? response.requestIndex : index;
    const request = requests[requestIndex];
    if (!request) return;
    try {
      generated = generated.concat(parseGeneratedProblemArray_(response.text, request.planItems));
    } catch (error) {
      // Parsed groups are kept. Missing plan numbers are retried individually by validation.
    }
  });
  return generated;
}

function getGeneratedProblemImageIssue_(generatedItem, planItem) {
  const problemText = String(
    generatedItem && (generatedItem.problem || generatedItem.body) || ''
  ).replace(/\[그림\s*필요\s*:/g, '[이미지 필요:');
  const normalizedProblemText = normalizeImagePromptBlocks_(problemText);
  const imageCount = (normalizedProblemText.match(/\[이미지\s*필요\s*:/g) || []).length;
  const blocks = normalizedProblemText.match(/\[IMAGE_PROMPT\s*:\s*[\s\S]*?\]/gi) || [];
  if (planItem && planItem.imageRequired === true && imageCount < 1) {
    return '이미지 문항 계획인데 [이미지 필요]가 없습니다.';
  }
  if (planItem && planItem.imageRequired === true && blocks.length < 1) {
    return '이미지 문항 계획인데 IMAGE_PROMPT가 없습니다.';
  }
  if (planItem && planItem.imageRequired === false && (imageCount > 0 || blocks.length > 0)) {
    return '이미지 없는 문항 계획에 이미지 블록이 포함되었습니다.';
  }
  if (planItem && planItem.imageRequired === false && hasUnresolvedFigureReference_(normalizedProblemText)) {
    return '이미지 없는 문항이 그림이나 제시 도형을 참조하지만 도형 조건을 본문에 완전히 설명하지 않았습니다.';
  }
  if (blocks.length < imageCount) return '이미지 문항에 IMAGE_PROMPT가 없습니다.';
  if (planItem && planItem.imageRequired === true && blocks.length !== 1) {
    return '이미지 문항 하나에는 IMAGE_PROMPT가 정확히 1개여야 합니다.';
  }
  if (planItem && planItem.imageRequired === true) {
    const expectedTemplate = String(planItem.imageTemplate || '').trim().toLowerCase();
    const templateMatch = String(blocks[0] || '').match(/\btemplate\s*=\s*([a-z0-9_]+)\b/i);
    const actualTemplate = templateMatch ? String(templateMatch[1] || '').toLowerCase() : '';
    if (!expectedTemplate || actualTemplate !== expectedTemplate) {
      return '지정 템플릿 불일치: ' + expectedTemplate + '가 필요하지만 '
        + (actualTemplate || 'template 없음') + '을 출력했습니다.';
    }
    let requiredFields = String(planItem.imageRequiredFields || '')
      .split(',')
      .map(field => field.trim())
      .filter(Boolean);
    if (expectedTemplate === 'parabola_labeled_xintercepts') {
      requiredFields = requiredFields.filter(field => field !== 'curve_label');
    }
    const missingFields = requiredFields.filter(field => {
      return !(new RegExp('^\\s*' + escapeRegExp_(field) + '\\s*=\\s*\\S+', 'im')).test(blocks[0]);
    });
    if (missingFields.length) {
      return expectedTemplate + ' 필수 항목 누락: ' + missingFields.join(', ');
    }
    if (expectedTemplate === 'linear_two_lines_xaxis_square') {
      const roleIssue = getLinearTwoLinesSquareRoleIssue_(normalizedProblemText, blocks[0]);
      if (roleIssue) return roleIssue;
    }
  }

  for (let index = 0; index < blocks.length; index += 1) {
    const error = getImagePromptBlockError_(blocks[index], index + 1);
    if (error) return error;
  }
  return '';
}

function getLinearTwoLinesSquareRoleIssue_(problemText, imagePromptBlock) {
  const leftEquation = getImagePromptValue_(imagePromptBlock, 'equation_left');
  const rightEquation = getImagePromptValue_(imagePromptBlock, 'equation_right');
  if (!leftEquation || !rightEquation) return '';

  const text = normalizeRoleCheckText_(problemText);
  const left = normalizeRoleCheckText_(leftEquation);
  const right = normalizeRoleCheckText_(rightEquation);
  const cThenDMarker = text.indexOf('점C,D는각각');
  if (cThenDMarker >= 0) {
    const tail = text.slice(cThenDMarker);
    const rightIndex = tail.indexOf(right);
    const leftIndex = tail.indexOf(left);
    if (rightIndex < 0 || leftIndex < 0 || rightIndex > leftIndex) {
      return 'linear_two_lines_xaxis_square 점 역할 불일치: C는 equation_right, D는 equation_left 위에 있어야 합니다.';
    }
    return '';
  }

  const dThenCMarker = text.indexOf('점D,C는각각');
  if (dThenCMarker >= 0) {
    const tail = text.slice(dThenCMarker);
    const leftIndex = tail.indexOf(left);
    const rightIndex = tail.indexOf(right);
    if (leftIndex < 0 || rightIndex < 0 || leftIndex > rightIndex) {
      return 'linear_two_lines_xaxis_square 점 역할 불일치: D는 equation_left, C는 equation_right 위에 있어야 합니다.';
    }
    return '';
  }

  const dRole = text.indexOf('점D') >= 0 && text.indexOf(left, text.indexOf('점D')) >= 0;
  const cRole = text.indexOf('점C') >= 0 && text.indexOf(right, text.indexOf('점C')) >= 0;
  if (!dRole || !cRole) {
    return 'linear_two_lines_xaxis_square 본문에 D=equation_left, C=equation_right 관계가 명확하지 않습니다.';
  }
  return '';
}

function getImagePromptValue_(block, key) {
  const match = String(block || '').match(
    new RegExp('^\\s*' + escapeRegExp_(key) + '\\s*=\\s*([^\\r\\n\\]]+)', 'im')
  );
  return match ? String(match[1] || '').trim() : '';
}

function normalizeRoleCheckText_(value) {
  return String(value || '')
    .replace(/\[IMAGE_PROMPT\s*:[\s\S]*?\]/gi, '')
    .replace(/\[수식\s*:\s*/g, '')
    .replace(/\]/g, '')
    .replace(/\*/g, '')
    .replace(/[–—−]/g, '-')
    .replace(/\s+/g, '');
}

function hasUnresolvedFigureReference_(problemText) {
  const text = String(problemText || '')
    .replace(/\[이미지\s*필요\s*:[\s\S]*?\]/gi, ' ')
    .replace(/\[IMAGE_PROMPT\s*:[\s\S]*?\]/gi, ' ');
  return /(?:다음|아래|오른쪽)\s*(?:그림|도형|그래프|좌표평면|정사각형|직사각형|삼각형|사다리꼴|원)|그림과\s*같이|그림에서|도형에서/.test(text);
}

function getImagePromptBlockError_(block, index) {
  if (/,\s*[a-z_][a-z0-9_]*\s*=/i.test(block)) {
    return 'IMAGE_PROMPT ' + index + '번은 key=value 항목마다 줄바꿈해야 합니다.';
  }
  const templateMatch = block.match(/\btemplate\s*=\s*([a-z0-9_]+)\b/i);
  if (templateMatch) {
    const template = String(templateMatch[1] || '').toLowerCase();
    if (getImplementedImageTemplateNames_().indexOf(template) < 0) {
      return '지원되지 않는 IMAGE_PROMPT template: ' + template;
    }
    if (template === 'rectangle_cross_road'
        && (!/\bwidth\s*=/i.test(block)
            || !/\bheight\s*=/i.test(block)
            || !/\broad_width\s*=/i.test(block))) {
      return 'rectangle_cross_road IMAGE_PROMPT ' + index
        + '번에 width, height, road_width가 필요합니다.';
    }
    if (template === 'linear_parameter_triangle_cases'
        && (!/\bequations\s*=/i.test(block)
            || !/\bparameter\s*=/i.test(block)
            || !/\bparameter_values\s*=/i.test(block))) {
      return 'linear_parameter_triangle_cases IMAGE_PROMPT ' + index
          + '번에 equations, parameter, parameter_values가 필요합니다.';
    }
    if (template === 'multiple_choice_parabola_position') {
      const choicesMatch = block.match(/\bchoices\s*=\s*([^\r\n\]]+)/i);
      if (!choicesMatch) {
        return 'multiple_choice_parabola_position IMAGE_PROMPT ' + index
          + '번에 choices가 필요합니다.';
      }
      const choicesText = String(choicesMatch[1] || '').trim();
      if (/^\s*\[/.test(choicesText) || /["{}]/.test(choicesText)) {
        return 'multiple_choice_parabola_position IMAGE_PROMPT ' + index
          + '번의 choices는 JSON/배열이 아니라 세미콜론으로 구분한 식 5개여야 합니다.';
      }
      const choices = choicesText.split(';').map(value => value.trim()).filter(Boolean);
      if (choices.length !== 5 || choices.some(value => !/^y\s*=/i.test(value))) {
        return 'multiple_choice_parabola_position IMAGE_PROMPT ' + index
          + '번의 choices에는 y=... 형식의 이차함수 식 5개가 필요합니다.';
      }
      const unresolvedChoice = choices.some(value => {
        const expression = value.replace(/^y\s*=/i, '');
        const allowed = expression
          .replace(/\b(?:sin|cos|tan|sqrt|abs|pi)\b/gi, '')
          .replace(/[xy]/gi, '');
        return /[a-wz]/i.test(allowed);
      });
      if (unresolvedChoice) {
        return 'multiple_choice_parabola_position IMAGE_PROMPT ' + index
          + '번의 choices에는 a, b, p, q 같은 미정계수 없이 숫자가 확정된 식만 필요합니다.';
      }
    }
    if (template === 'regular_polygon_chain_sequence') {
      const stageCountsMatch = block.match(/\bstage_counts\s*=\s*([^\r\n\]]+(?:\][^\r\n]*)?)/i);
      const stageCountsText = stageCountsMatch ? String(stageCountsMatch[1] || '').trim() : '';
      const stageNumbers = stageCountsText.match(/\d+/g) || [];
      if (stageNumbers.length < 2) {
        return 'regular_polygon_chain_sequence IMAGE_PROMPT ' + index
          + '번의 stage_counts는 [1,2,3]처럼 연결 과정을 나타내는 2개 이상의 단계가 필요합니다.';
      }
    }
    const requiredTemplateFields = {
      past_exam_image: ['source_id'],
      parabola_basic_shape: ['equation'],
      // The renderer falls back to the equation text when curve_label is absent.
      // Do not discard an otherwise valid paid generation for this cosmetic label.
      parabola_labeled_xintercepts: ['equation'],
      parabola_family_origin: ['equations'],
      parabola_vertex_yintercept_origin_triangle: ['vertex', 'y_intercept'],
      three_semicircles: ['diameter', 'split'],
      circle_with_two_semicircles: ['outer_diameter', 'left_inner_diameter', 'right_inner_diameter'],
      unit_quarter_circle_trig: ['angle'],
      parabola_inscribed_square: ['equation', 'x_left', 'x_right', 'y_bottom'],
      two_parabolas_axis_aligned_square: ['equation_left', 'equation_right', 'square_side'],
      coordinate_parallelogram: ['points'],
      two_origin_parabolas_parallelogram: ['equation1', 'equation2', 'vertical_x'],
      two_origin_parabolas_vertical_line_ratio: ['equation1', 'equation2', 'vertical_x'],
      parabola_yaxis_xpositive_parallelogram: ['equation', 'y_axis_y'],
      two_parabolas_shared_vertex_intersections: ['equation1', 'equation2'],
      rectangle_square_similar_split: ['width', 'height', 'square_side'],
      rectangle_inner_slanted_quadrilateral: ['width', 'height', 'top_point', 'bottom_point'],
      open_box_net_rectangular_paper: ['paper_width', 'paper_height', 'cut_side'],
      open_box_net_equal_cuts: ['paper_side', 'cut_side'],
      moving_points_rectangle_triangle: [
        'rectangle_width', 'rectangle_height', 'point_p_speed', 'point_q_speed'
      ],
      moving_points_right_triangle: [
        'vertical_leg', 'horizontal_leg', 'point_p_speed', 'point_q_speed'
      ],
      activity_calorie_table: ['activities', 'calories_per_10min'],
      linear_sign_diagram: ['slope_sign', 'y_intercept_sign'],
      regular_polygon_chain_sequence: ['sides', 'side', 'stage_counts'],
      moving_point_rectangle_trapezoid: ['rectangle_width', 'rectangle_height', 'point_speed'],
      linear_vertical_line_position: ['x_value'],
      linear_two_lines_labeled_points: ['equation1', 'equation2', 'point_a_x', 'point_b_x'],
      linear_two_lines_xaxis_square: ['equation_left', 'equation_right'],
      line_to_parabola_quadrant_match: ['line_equation', 'parabola_form']
    };
    const requiredFields = requiredTemplateFields[template] || [];
    const missingFields = requiredFields.filter(field => {
      return !(new RegExp('\\b' + field + '\\s*=', 'i')).test(block);
    });
    if (missingFields.length) {
      return template + ' IMAGE_PROMPT ' + index + '번에 '
        + missingFields.join(', ') + ' 항목이 필요합니다.';
    }
    if (template === 'past_exam_image') {
      const valueFormatError = getPastExamImageValueFormatError_(block, index);
      if (valueFormatError) return valueFormatError;
    }
    if (hasUnresolvedImagePromptVariables_(block, template)) {
      return template + ' IMAGE_PROMPT ' + index
        + '번에 k, a, b, x, y 같은 값이 정해지지 않은 변수가 남아 있습니다.';
    }
    if (template === 'three_semicircles') {
      const diameter = getImagePromptFieldValue_(block, 'diameter');
      const split = getImagePromptFieldValue_(block, 'split');
      if (!isFiniteImagePromptNumber_(diameter) || !isFiniteImagePromptNumber_(split)) {
        return 'three_semicircles IMAGE_PROMPT ' + index
          + '번의 diameter와 split은 렌더링용 실제 숫자여야 합니다.';
      }
    }
    return '';
  }

  if (!/\btype\s*=\s*(geometry|coordinate_plane)\b/i.test(block)) {
    return 'IMAGE_PROMPT ' + index + '번에 지원되는 template 또는 type이 필요합니다.';
  }
  if (/\btype\s*=\s*geometry\b/i.test(block)
      && (!/\bshape\s*=/i.test(block)
          || !/\b(coordinates|center)\s*=/i.test(block))) {
    return 'geometry IMAGE_PROMPT ' + index + '번에 shape와 coordinates/center가 필요합니다.';
  }
  if (/\btype\s*=\s*coordinate_plane\b/i.test(block)
      && !/\b(equation|equations|equation\d+|points)\s*=/i.test(block)) {
    return 'coordinate_plane IMAGE_PROMPT ' + index
      + '번에 equation 또는 points가 필요합니다.';
  }
  const rangeFields = ['x_range', 'y_range'];
  for (let rangeIndex = 0; rangeIndex < rangeFields.length; rangeIndex += 1) {
    const field = rangeFields[rangeIndex];
    const value = getImagePromptFieldValue_(block, field);
    if (value && !/^\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*$/.test(value)) {
      return 'coordinate_plane IMAGE_PROMPT ' + index
        + '번의 ' + field + '는 최소값,최대값 형식의 숫자여야 합니다.';
    }
  }
  if (/\btype\s*=\s*coordinate_plane\b/i.test(block)) {
    const equationMatch = block.match(/\bequation\s*=\s*([^\r\n\]]+)/i);
    if (equationMatch) {
      const unresolved = String(equationMatch[1] || '')
        .replace(/\b(?:sqrt|sin|cos|tan|log|ln|pi|x|y)\b/gi, '')
        .match(/[A-Za-z]/);
      if (unresolved) {
        return 'coordinate_plane IMAGE_PROMPT ' + index
          + '번의 equation에 값이 정해지지 않은 문자가 있습니다.';
      }
    }

    const pointsMatch = block.match(/\bpoints\s*=\s*([^\r\n\]]+)/i);
    const pointCount = pointsMatch
      ? (String(pointsMatch[1] || '').match(/\(\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\)/g) || []).length
      : 0;
    const hasTopology = /\b(segments?|polygon|polygons|rectangle_points|region|connect|connections|edges)\s*=/i.test(block);
    if (pointCount >= 3 && !hasTopology) {
      return 'coordinate_plane IMAGE_PROMPT ' + index
        + '번은 점이 3개 이상이지만 segments/polygon/template 연결 정보가 없습니다.';
    }
  }
  return '';
}

function getPastExamImageValueFormatError_(block, index) {
  const ignoredKeys = {
    template: true,
    source_id: true,
    past_exam_image_id: true,
    pastexamimageid: true,
    exam_image_id: true,
    library_root: true,
    libraryroot: true
  };
  const lines = String(block || '').split(/\r?\n/);
  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const match = lines[lineIndex].match(/^\s*([a-z_][a-z0-9_]*)\s*=\s*(.*?)\s*$/i);
    if (!match) continue;
    const key = String(match[1] || '').toLowerCase();
    if (ignoredKeys[key]) continue;
    const value = String(match[2] || '').trim();
    if (/\d+\.\d{3,}/.test(value)) {
      return 'past_exam_image IMAGE_PROMPT ' + index
        + '번의 ' + match[1] + ' 값은 긴 소수 대신 정수 또는 기약분수로 작성해야 합니다.';
    }
  }
  return '';
}

function hasUnresolvedImagePromptVariables_(block, template) {
  const normalizedTemplate = String(template || '').toLowerCase();
  if (normalizedTemplate === 'past_exam_image') return false;
  if (normalizedTemplate === 'multiple_choice_parabola_position') return false;
  const ignoredFields = {
    annotations: true,
    labels: true,
    label: true,
    time_label: true,
    curve_label: true
  };
  const allowedEquationLetters = {
    x: true,
    y: true,
    sin: true,
    cos: true,
    tan: true,
    sqrt: true,
    pi: true,
    abs: true
  };
  const isEquationLikeField = key => {
    return key === 'equations'
      || key.indexOf('equation') === 0
      || key === 'line_equation'
      || key === 'parabola_form';
  };
  return String(block || '').split(/\r?\n/).some(line => {
    const match = line.match(/^\s*([a-z_][a-z0-9_]*)\s*=\s*(.*?)\s*$/i);
    if (!match) return false;
    const key = String(match[1] || '').toLowerCase();
    if (key === 'template' || key === 'type' || key === 'parameter' || ignoredFields[key]) return false;
    const value = String(match[2] || '').replace(/\b[A-Z]\s*(?=\()/g, '');
    if (isEquationLikeField(key)) {
      const unresolvedEquationText = value
        .replace(/\b(?:sqrt|sin|cos|tan|log|ln|pi|abs)\b/gi, '')
        .replace(/[xy]/gi, '')
        .replace(/[0-9\s+\-*/^().,;=]/g, '');
      if (/[A-Za-z]/.test(unresolvedEquationText)) return true;
    }
    const words = value.match(/[A-Za-z]+/g) || [];
    return words.some(word => {
      const normalized = word.toLowerCase();
      if (isEquationLikeField(key) && allowedEquationLetters[normalized]) return false;
      return /^[a-z]$/i.test(word);
    });
  });
}

function escapeRegExp_(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function getImagePromptFieldValue_(block, fieldName) {
  const match = String(block || '').match(
    new RegExp('^\\s*' + fieldName + '\\s*=\\s*(.*?)\\s*$', 'im')
  );
  return match ? String(match[1] || '').trim() : '';
}

function isFiniteImagePromptNumber_(value) {
  return /^-?\d+(?:\.\d+)?$/.test(String(value || '').trim());
}

function getImagePromptKnownKeys_() {
  return [
    'template', 'type', 'shape', 'coordinates', 'center', 'radius', 'points',
    'rectangle_points', 'segments', 'segment', 'polygon', 'polygons', 'region',
    'connect', 'connections', 'edges', 'equation', 'equations', 'equation1',
    'equation2', 'equation_top', 'equation_bottom', 'equation_left',
    'equation_right', 'line_equation', 'parabola_form', 'choices',
    'x_range', 'y_range', 'x_value', 'x_left', 'x_right', 'y_bottom',
    'y_axis_y', 'vertical_x', 'point_a_x', 'point_b_x', 'width', 'height',
    'road_width', 'square_side', 'width_label', 'height_label',
    'rectangle_width', 'rectangle_height', 'point_speed', 'point_p_speed',
    'point_q_speed', 'vertical_leg', 'horizontal_leg', 'base', 'side',
    'sides', 'stage_counts', 'speed', 'diameter', 'split', 'outer_diameter',
    'left_inner_diameter', 'right_inner_diameter', 'outer_radius',
    'inner_radius', 'angle', 'activities', 'calories_per_10min',
    'parameter', 'parameter_values', 'slope_sign', 'y_intercept_sign',
    'curve_label', 'source_id', 'past_exam_image_id', 'library_root'
  ];
}

function normalizeImagePromptKeyValueLines_(body) {
  const keyPattern = new RegExp(
    '(?:^|[\\s,]+)(' + getImagePromptKnownKeys_().join('|') + ')\\s*=',
    'ig'
  );
  const commaNormalized = String(body || '').replace(
    /,\s*([A-Za-z_][A-Za-z0-9_]*)\s*=/g,
    '\n$1='
  );
  return commaNormalized.split(/\r?\n/).map(line => {
    const positions = [];
    let match;
    while ((match = keyPattern.exec(line)) !== null) {
      const keyOffset = match[0].search(/[A-Za-z_]/);
      const position = match.index + Math.max(keyOffset, 0);
      if (positions.indexOf(position) < 0) positions.push(position);
    }
    keyPattern.lastIndex = 0;
    if (positions.length <= 1 || positions[0] !== 0) return line;

    return positions.map((position, index) => {
      const nextPosition = index + 1 < positions.length ? positions[index + 1] : line.length;
      return line.slice(position, nextPosition).replace(/^[\s,]+/, '').replace(/,\s*$/, '').trim();
    }).filter(Boolean).join('\n');
  }).join('\n');
}

function normalizeImagePromptBlocks_(text) {
  const canonical = wrapLooseImagePromptBlocks_(
    String(text || '').replace(
      /\[\s*IMAGE[_\s-]*PROMPT\s*(?:\(\s*\d+\s*\)|\d+)?\s*:\s*([\s\S]*?)\]/gi,
      (whole, body) => '[IMAGE_PROMPT:\n' + normalizeImagePromptKeyValueLines_(body).trim() + '\n]'
    )
  );
  const lineNormalized = canonical.replace(
    /\[IMAGE_PROMPT\s*:\s*([\s\S]*?)\]/gi,
    (whole, body) => '[IMAGE_PROMPT:\n' + normalizeImagePromptKeyValueLines_(body).trim() + '\n]'
  );
  return lineNormalized.replace(
    /\[IMAGE_PROMPT\s*:\s*([\s\S]*?)\]/gi,
    (whole, body) => {
      if (!/\btype\s*=\s*coordinate_plane\b/i.test(body)) return whole;

      const equationValues = [];
      const keptLines = [];
      String(body || '').split(/\r?\n/).forEach(line => {
        const match = line.match(/^\s*(equations?|equation\d+)\s*=\s*(.+?)\s*$/i);
        if (match) {
          equationValues.push(String(match[2] || '').trim());
        } else {
          keptLines.push(line);
        }
      });
      if (equationValues.length) {
        keptLines.push('equation=' + equationValues.join('; '));
      }
      return '[IMAGE_PROMPT:\n' + keptLines.join('\n').trim() + '\n]';
    }
  );
}

function normalizePastExamParabolaChoicePrompts_(text) {
  return String(text || '').replace(
    /\[IMAGE_PROMPT\s*:\s*([\s\S]*?)\]/gi,
    (whole, body) => {
      const template = getImagePromptFieldValue_(whole, 'template').toLowerCase();
      const type = getImagePromptFieldValue_(whole, 'type').toLowerCase();
      if (template === 'multiple_choice_parabola_position') return whole;
      if (type !== 'coordinate_plane' && template !== 'parabola_basic_shape') return whole;

      const equationText = getImagePromptFieldValue_(whole, 'equation')
        || getImagePromptFieldValue_(whole, 'choices');
      if (!equationText) return whole;

      const choices = String(equationText || '')
        .split(';')
        .map(value => String(value || '')
          .replace(/\{[^}]*\}/g, '')
          .replace(/^\s*[①②③④⑤⑥⑦⑧⑨⑩]\s*/, '')
          .trim())
        .filter(Boolean);
      const quadraticChoices = choices.filter(value => /x\s*(?:\^2|²)/i.test(value));
      if (choices.length !== 5 || quadraticChoices.length !== 5) return whole;

      return '[IMAGE_PROMPT:\n'
        + 'template=multiple_choice_parabola_position\n'
        + 'choices=' + choices.slice(0, 5).join('; ')
        + '\n]';
    }
  );
}

function normalizeGeometryImagePrompts_(text) {
  return String(text || '').replace(
    /\[IMAGE_PROMPT\s*:\s*([\s\S]*?)\]/gi,
    (whole, body) => {
      if (!/\btype\s*=\s*geometry\b/i.test(body)) return whole;

      const hasShape = /\bshape\s*=/i.test(body);
      const hasCoordinates = /\b(coordinates|center)\s*=/i.test(body);
      const points = getImagePromptFieldValue_(whole, 'points')
        || getImagePromptFieldValue_(whole, 'rectangle_points');
      const center = getImagePromptFieldValue_(whole, 'center');
      const radius = getImagePromptFieldValue_(whole, 'radius');
      const polygon = getImagePromptFieldValue_(whole, 'polygon');
      const segments = getImagePromptFieldValue_(whole, 'segments');
      const equation = getImagePromptFieldValue_(whole, 'equation')
        || getImagePromptFieldValue_(whole, 'equations')
        || getImagePromptFieldValue_(whole, 'equation1')
        || getImagePromptFieldValue_(whole, 'equation2');

      if (!hasCoordinates && equation) {
        const convertedLines = String(body || '').split(/\r?\n/)
          .map(line => line.replace(/\btype\s*=\s*geometry\b/i, 'type=coordinate_plane'))
          .filter(line => !/^\s*shape\s*=/i.test(line));
        return '[IMAGE_PROMPT:\n' + convertedLines.join('\n').trim() + '\n]';
      }

      const additions = [];
      if (!hasShape) {
        if (center || radius) {
          additions.push('shape=circle');
        } else if (polygon || /(?:^|,)\s*[A-Za-z]\s*,\s*[A-Za-z]\s*,\s*[A-Za-z]\s*,\s*[A-Za-z]/.test(segments)) {
          additions.push('shape=quadrilateral');
        } else if (points) {
          const pointCount = (String(points).match(/[A-Za-z]\s*\(/g) || []).length;
          additions.push('shape=' + (pointCount >= 4 ? 'quadrilateral' : 'triangle'));
        }
      }
      if (!hasCoordinates && points) {
        additions.push('coordinates=' + points);
      }

      if (!additions.length) return whole;
      return '[IMAGE_PROMPT:\n' + String(body || '').trim()
        + '\n' + additions.join('\n') + '\n]';
    }
  );
}

function wrapLooseImagePromptBlocks_(text) {
  const lines = String(text || '').replace(/\r/g, '').split('\n');
  const output = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const startMatch = line.match(/^\s*IMAGE[_\s-]*PROMPT\s*(?:\(\s*\d+\s*\)|\d+)?\s*:\s*(.*?)\s*$/i);
    if (!startMatch) {
      output.push(line);
      continue;
    }

    const bodyLines = [];
    const inlineBody = String(startMatch[1] || '').trim();
    if (inlineBody) bodyLines.push(inlineBody);

    let cursor = index + 1;
    while (cursor < lines.length) {
      const nextLine = lines[cursor];
      const trimmed = String(nextLine || '').trim();
      if (!trimmed) break;
      if (/^\s*(?:문항\d+\.|출처유형\s*:|문제\s*:|정답\s*:|해설\s*:|\[이미지\s*필요\s*:|\[IMAGE[_\s-]*PROMPT)/i.test(trimmed)) {
        break;
      }
      if (!/^\s*[A-Za-z0-9_가-힣]+\s*[=:]/.test(trimmed)) break;
      bodyLines.push(nextLine);
      cursor += 1;
    }

    if (bodyLines.length) {
      output.push('[IMAGE_PROMPT:');
      bodyLines.forEach(bodyLine => output.push(bodyLine));
      output.push(']');
      index = cursor - 1;
    } else {
      output.push('[IMAGE_PROMPT:');
      output.push(']');
    }
  }
  return output.join('\n');
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
  const imagePromptSet = toNumberSet_(issues.imagePromptNumbers);

  return plan.items.map(item => {
    const number = Number(item.number);
    const existing = generatedByNumber[number] || {};
    if (missingSet[number]) {
      return buildReviewProblemItem_(number, item, 'AI 응답에서 해당 번호를 찾지 못했습니다.');
    }
    if (draftLeakSet[number]) {
      return buildReviewProblemItem_(number, item, '초안 작성 과정이 섞여 있어 수동 검수가 필요합니다.');
    }
    if (imagePromptSet[number]) {
      const imageReason = issues.imagePromptReasons && issues.imagePromptReasons[number];
      return buildReviewProblemItem_(
        number,
        item,
        imageReason || '이미지 템플릿이 누락되었거나 올바르지 않습니다.'
      );
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

function getStandardProblemNumberingPromptRules_() {
  return [
    '- 문제 안의 소문항 번호는 반드시 (1), (2), (3)처럼 반괄호 숫자 형식으로 쓴다.',
    '- 소문항에 1), 1., [1], 가), 가. 형식을 사용하지 마라.',
    '- 객관식 보기는 반드시 ①, ②, ③, ④, ⑤처럼 원 안의 숫자 형식으로 쓴다.',
    '- 객관식 보기에 (1), (2), 1), 2), 1., 2. 형식을 사용하지 마라.',
    '- 객관식 보기의 각 선택지를 해설에서 언급할 때도 (1), (2), 1), 2)가 아니라 반드시 ①, ②, ③, ④, ⑤로 쓴다.',
    '- 소문항 번호 (1), (2), (3)과 객관식 보기 ①, ②, ③, ④, ⑤를 서로 바꾸어 쓰지 마라.',
    '- 정답과 해설에서도 소문항을 구분할 때 문제 본문과 동일하게 (1), (2), (3) 형식을 유지하라.'
  ];
}

function getGeneratedNumberingStyleIssue_(text) {
  const value = String(text || '');
  if (/(^|\n)\s*(?:\d+\)|\d+\.|\[\d+\]|[가-하][.)])\s+/.test(value)) {
    return '소문항 번호는 (1), (2), (3) 형식만 사용해야 합니다.';
  }
  return '';
}

function normalizeGeneratedNumberingStyle_(text) {
  const circled = ['', '\u2460', '\u2461', '\u2462', '\u2463', '\u2464'];
  const lines = String(text || '').split('\n');

  for (let i = 0; i < lines.length; i++) {
    const run = [];
    for (let j = i; j < lines.length; j++) {
      const match = String(lines[j] || '').match(/^(\s*)(?:([1-5])[\).]|\[([1-5])\])\s+(.*)$/);
      if (!match) break;
      const number = Number(match[2] || match[3]);
      if (run.length && number !== run[run.length - 1].number + 1) break;
      run.push({ index: j, number: number });
    }
    if (run.length >= 3) {
      run.forEach(item => {
        lines[item.index] = String(lines[item.index]).replace(
          /^(\s*)(?:[1-5][\).]|\[[1-5]\])\s+/,
          '$1' + circled[item.number] + ' '
        );
      });
      i = run[run.length - 1].index;
    }
  }

  return lines.map(line => {
    return String(line || '').replace(
      /^(\s*)(?:([1-9]\d*)[\).]|\[([1-9]\d*)\])\s+/,
      (all, spaces, n1, n2) => spaces + '(' + (n1 || n2) + ') '
    );
  }).join('\n');
}

function buildSimilarProblemsPrompt_(studentName, examName, wrongProblems, reportText, rulesByType, planItems) {
  const compactWrongProblems = groupTwinWrongProblems_(wrongProblems).map(group => ({
    problemNumber: group.baseProblemNumber,
    sourceProblemNumbers: group.sourceProblemNumbers,
    subproblemCount: group.subproblemCount,
    types: group.types,
    unit1: group.unit1,
    unit2: group.unit2,
    formType: group.formType,
    problemText: group.sourceProblemText,
    answers: group.answers,
    hasImage: group.hasImage,
    imageTemplate: group.imageTemplateHint ? group.imageTemplateHint.template : '',
    imageRequiredFields: group.imageTemplateHint ? group.imageTemplateHint.requiredFields : ''
  }));
  return [
    '너는 20년 경력의 베테랑 중고등학교 수학문제 출제자다.',
    '요구사항:',
    getStandardProblemNumberingPromptRules_().join('\n'),
    '- Multiple-choice options must be meaningfully different. Do not repeat the same first tuple component, same expression, or same common value in all five options; move common values into the problem statement and vary only discriminating values.',
    '- 반드시 문항 계획의 약점유형, 생성유형, 문항번호, 난이도를 그대로 따른다.',
    '- 쌍둥이문항은 원본문항의 문항형식을 유지한다. 원본이 5지선다형이면 5지선다형, 단답형이면 단답형, 서술형이면 서술형으로만 만든다.',
    '- sourceProblemNumbers가 같은 기본 문제번호의 소문항들은 하나의 원본문항 묶음이다.',
    '- 소문항 묶음은 각각 별도 문제로 분리하지 말고, 원본과 같은 개수의 (1), (2), (3) 소문항을 포함하는 쌍둥이문항 한 세트로 작성하라.',
    '- 문항 계획의 sourceSubproblemCount가 2 이상이면 생성 결과 한 문항 안에 정확히 그 수만큼의 소문항과 각 소문항의 정답·해설을 함께 작성하라.',
    '- 생성유형이 5지선다형이면 반드시 ①~⑤ 선택지를 정확히 5개 작성하라. 단답형과 서술형에는 ①~⑤ 선택지를 쓰지 마라.',
    '- 단답형이나 서술형 문제를 만들고 생성유형을 5지선다형처럼 보이게 하지 마라. 5지선다형에는 반드시 의미 있게 다른 선택지 5개가 있어야 한다.',
    '- 문제에 별도의 <보기>가 필요한 경우 반드시 <보기>와 </보기> 태그로 감싸라. HWP 생성기는 이 블록을 1x1 표로 변환한다.',
    '- <보기> 안에는 보기 내용만 넣고 ①~⑤ 선택지는 <보기> 밖에 작성하라.',
    '- 선택지와 정답의 유리수는 소수로 쓰지 말고 기약분수 a/b 꼴로 작성하라. 원문이 소수 근삿값을 요구하는 삼각비표 문제처럼 명시된 경우에만 소수를 허용한다.',
    '- sourceSubproblemCount가 2 이상인 5지선다형은 각 소문항을 따로 묻고 숫자 하나만 고르게 하지 마라. 모든 소문항의 답을 순서대로 나열한 순서쌍 또는 답의 묶음 5개를 제시하고, 그중 옳게 짝지어진 것을 고르게 하라.',
    '- "계수 -1을 p로 잘못 보았다"는 원래 항 -y의 계수 -1 전체를 p로 바꿨다는 뜻이므로 잘못 본 항은 +py이다. -py로 쓰지 마라. 일반적으로 부호를 포함한 원래 계수 전체를 새 문자로 치환하라.',
    '- 문항 계획의 imageRequired가 true인 문항만 이미지 문항으로 만들고, [이미지 필요]와 IMAGE_PROMPT를 모두 출력하라.',
    '- 문항 계획의 imageRequired가 false인 문항에는 [이미지 필요], [그림 필요], IMAGE_PROMPT를 절대 출력하지 마라.',
    '- imageRequired=false인 문항은 그림을 보라는 표현을 쓰지 말고, 점의 위치, 도형의 종류, 변의 평행·수직 관계, 길이, 함수 위의 점 조건을 문제 본문에 모두 적어 그림 없이 독립적으로 풀 수 있게 하라.',
    '- imageRequired=false인 문항에서 "다음 그림", "아래 그림", "오른쪽 그림", "그림과 같이", "다음 정사각형", "다음 삼각형"처럼 보이지 않는 그림이나 도형을 가리키는 표현은 금지한다.',
    '- 이번 호출의 이미지 문항 수를 임의로 늘리거나 줄이지 마라.',
    '- imageRequired=true인 문항은 계획의 imageTemplate을 그대로 사용하라. 다른 템플릿을 고르거나 type=geometry/coordinate_plane으로 바꾸지 마라.',
    '- imageRequiredFields에 적힌 key를 하나도 빠뜨리지 말고, 각 key=value를 반드시 서로 다른 줄에 출력하라.',
    '- rectangle_square_similar_split은 width, height, square_side에 렌더링 비율용 실제 숫자를 넣고, 문제에 문자 길이가 제시되면 width_label, height_label에 인쇄할 표기(예: 6, x)를 반드시 따로 넣어라. 근삿값 소수를 길이 라벨로 인쇄하지 마라.',
    '- rectangle_inner_slanted_quadrilateral은 직사각형 ABCD에서 E, F가 양쪽 변 위에 있고 EBFD 또는 유사한 내부 사각형/평행사변형이 색칠되는 그림에 사용하라. width, height, top_point, bottom_point를 넣고 범용 geometry로 대체하지 마라.',
    '- linear_two_lines_xaxis_square는 왼쪽 위 꼭짓점 D가 equation_left 위에 있고 오른쪽 위 꼭짓점 C가 equation_right 위에 있다. 본문에서도 반드시 D=equation_left, C=equation_right 관계로 작성하라. C, D 순서로 서술하면 각각 equation_right, equation_left 순서여야 한다.',
    '- regular_polygon_chain_sequence는 연결 과정을 보여주는 템플릿이다. stage_counts에는 단일 숫자를 쓰지 말고 반드시 stage_counts=[1,2,3]처럼 2개 이상의 단계를 배열로 넣어라.',
    '- IMAGE_PROMPT의 첫 줄은 반드시 template=... 이어야 한다. imageTemplate=..., template:..., JSON 형식은 금지한다.',
    '- [이미지 필요: ...]를 출력한 문항에는 그 바로 다음 줄에 정확히 하나의 [IMAGE_PROMPT:] 블록을 출력하라. 둘 중 하나만 출력하는 것은 금지한다.',
    '- 이미지 문항은 문항 계획의 sourceProblemText와 imageStructure에 적힌 점의 역할, 연결 관계, 이동 시작점과 방향, 음영 영역을 그대로 유지하라.',
    '- 이미지 문항에서 바꿀 수 있는 것은 길이, 좌표, 계수, 각도 같은 수치뿐이다. 점 이름의 역할이나 도형의 위상은 바꾸지 마라.',
    '- 이미지의 구조와 템플릿은 이미 확정되어 있다. 너는 문제에 맞는 새 숫자, 확정된 식, 확정된 좌표만 정한다.',
    '- IMAGE_PROMPT에 k, a, b, p, q, 미정의 x, 미정의 y 같은 미정값을 남기지 마라. 렌더러가 즉시 그릴 수 있는 숫자로 모두 확정하라.',
    '- 문제 본문, 정답, 해설을 먼저 내부 검산한 뒤 그 결과와 정확히 일치하는 숫자만 IMAGE_PROMPT에 넣어라.',
    '- number 값은 반드시 문항 계획의 number를 그대로 사용하라. 각 호출 안에서 1, 2, 3으로 다시 번호를 매기지 말라.',
    '- 수식은 x^2가 아니라 유니코드 지수 형태로 작성한다.',
    '- 문제에 등장하는 수식은 반드시 [수식: ...] 형태로 작성한다.',
    '- LaTeX, $...$, \\( ... \\), \\frac, \\sqrt 표기는 쓰지 말고 x², √3, 3/4처럼 일반 텍스트와 유니코드 기호로 적어라.',
    '- 예: [수식: x² - 5x + 6 = 0], [수식: t = -b / 2a], [수식: √3 / 2]',
    '- 그래프, 도형, 삽화는 문항 계획의 imageRequired가 true일 때만 생성하고 [이미지 필요: ...] 형식으로 표시한다.',
    '- [이미지 필요: ...] 표시는 반드시 문항번호 바로 뒤에 적고, 그 다음 줄부터 문제 본문을 시작하라. 예: 문항1. [이미지 필요: y = 2x + 1 그래프]\\n다음 물음에 답하시오.',
    '- [이미지 필요: ...] 안에는 문제 본문을 보지 않아도 그림을 그릴 수 있도록 구조화된 속성 형식으로 필요한 정보를 구체적으로 적어라.',
    '- 도형 이미지는 가능한 한 도형=, 점=, 직각=, 변표시=, 각표시=, 평행=, 수직=, 원=, 중심=, 반지름=, 보조선= 같은 항목을 줄바꿈으로 적어라.',
    '- 그래프 이미지는 가능한 한 종류=좌표평면, 식=, 범위=, 점=, 교점=, 꼭짓점=, 축=, 영역=, 표시= 같은 항목을 줄바꿈으로 적어라.',
    '- 예: [이미지 필요:\\n도형=직각삼각형\\n점=A,B,C\\n직각=B\\n변표시=AB=3, BC=4\\n각표시=C=30도]',
    '- 예: [이미지 필요:\\n종류=좌표평면\\n식=y = x² + 1, y = x² - 8x + 17, y = -x² + 4x + 1\\n꼭짓점=A(0,1), B(4,1), C(2,5)\\n표시=삼각형 ABC]',
    '- 이미지가 필요한 문항은 [이미지 필요: ...] 바로 다음에 [IMAGE_PROMPT: ...] 블록을 반드시 출력하라.',
    '- IMAGE_PROMPT에는 설명 문장이 아니라 template 또는 type과 key=value만 적어라.',
    '- 원문 시험 문제를 그대로 복제하지 말고, 선정된 약점유형과 틀린 문항만 참고한다.',
    '- 쌍둥이_규칙의 생성규칙과 금지사항을 반드시 지킨다.',
    '- JSON을 사용하지 말고 아래 구분자 형식만 반복해서 반환하라. 마크다운, 코드블록, 전체 설명은 금지.',
    '- 영어로 된 Concept, Scenario, Difficulty, Details 같은 출제 계획/메타 설명을 절대 쓰지 말라.',
    '- 생각 과정, 출제 의도, 분석 메모, bullet point, 제목, 요약을 쓰지 말고 최종 문항만 한국어로 작성하라.',
    '- "잠시만", "다시 시도", "문제 설정을 변경", "계산이 깔끔하게", "역설계", "내가 만든 문제"처럼 초안 작성 과정이나 자기 수정 흔적을 절대 쓰지 말라.',
    '- 해설에는 최종 확정된 문제에 대한 풀이만 적어라. 문제를 만들다가 수정한 과정, 실패한 계산, 대안 문제는 쓰지 말라.',
    '- 계산 중 문제나 선택지를 바꿔야 한다고 판단해도 그 과정은 출력하지 말고 내부에서 처음부터 다시 작성하라.',
    '- "다시 풀이", "다시 확인", "숫자를 조정", "원래 오답", "수정된 선택지", "죄송합니다" 같은 문장이 한 글자라도 포함되면 응답은 폐기된다.',
    '- 최종 문제 본문에 나온 모든 수치와 식은 정답 및 해설에서 끝까지 동일해야 한다.',
    '- 해설은 핵심 식과 결론만 최대 4줄로 작성하라. 문제 조건을 다시 길게 설명하지 마라.',
    '- 풀이 단계 번호, 같은 계산의 반복, 검산 과정, 선택지별 오답 설명은 쓰지 마라.',
    '- 5지선다형과 단답형은 가능하면 2~3줄, 서술형도 4줄 이내로 끝내라.',
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
    '===문항_END===',
    '',
    '이번 요청 데이터:',
    '학생명: ' + studentName,
    '시험명: ' + examName,
    '분석보고서 참고자료: ' + (reportText || '분석보고서 파일이 없으므로 오답 유형만 참고한다.'),
    '오답 목록(JSON): ' + JSON.stringify(compactWrongProblems),
    '유형별 생성 규칙(JSON): ' + JSON.stringify(rulesByType),
    '이번 호출에서 생성할 문항 계획(JSON): ' + JSON.stringify(planItems)
  ].join('\n');
}

function buildTwinGenerationPlan_(wrongProblems, teacherScope, rulesByType) {
  const sourceGroups = groupTwinWrongProblems_(wrongProblems);
  const groupKeys = sourceGroups.map(group => group.baseProblemNumber);
  const weakTypes = unique_(sourceGroups.reduce((types, group) => types.concat(group.types), []));
  const countByGroup = distributeTwinCountsByWeakType_(sourceGroups, 2);
  const totalCount = groupKeys.reduce((sum, key) => sum + Number(countByGroup[key] || 0), 0);
  const formOrder = ['5지선다형', '단답형', '서술형'];
  const itemsWithoutNumber = [];

  sourceGroups.forEach(group => {
    const count = Number(countByGroup[group.baseProblemNumber] || 0);
    const form = group.formType || '단답형';
    for (let i = 0; i < count; i++) {
      itemsWithoutNumber.push({
        sourceGroupKey: group.baseProblemNumber,
        sourceProblemNumber: group.baseProblemNumber,
        sourceProblemNumbers: group.sourceProblemNumbers,
        sourceSubproblemCount: group.subproblemCount,
        sourceProblemText: group.sourceProblemText,
        sourceAnswers: group.answers,
        sourceTypes: group.types,
        weakType: group.types.join(' / '),
        formType: form
      });
    }
  });

  const numbered = [];
  formOrder.forEach(form => {
    const formItems = shuffle_(itemsWithoutNumber.filter(item => item.formType === form));
    formItems.forEach((item, index) => {
      numbered.push({
        number: numbered.length + 1,
        formOrdinal: index + 1,
        sourceGroupKey: item.sourceGroupKey,
        sourceProblemNumber: item.sourceProblemNumber,
        sourceProblemNumbers: item.sourceProblemNumbers,
        sourceSubproblemCount: item.sourceSubproblemCount,
        sourceProblemText: item.sourceProblemText,
        sourceAnswers: item.sourceAnswers,
        sourceTypes: item.sourceTypes,
        weakType: item.weakType,
        formType: item.formType,
        difficulty: (index + 1) % 2 === 1 ? '중' : randomChoice_(['상', '하'])
      });
    });
  });

  const imageSources = [];
  const seenImageGroups = {};
  sourceGroups.forEach(group => {
    const imageProblem = group.problems.find(problem => problem.hasImage) || group.problems[0];
    const sourceHint = group.imageTemplateHint;
    const rule = (rulesByType || {})[imageProblem.type] || {};
    const explicitTemplate = String(rule.imageTemplate || '').trim();
    const explicitFields = String(rule.imageRequiredFields || '').trim();
    let hint = null;
    if (sourceHint) {
      hint = sourceHint;
    } else if (explicitTemplate && group.hasImage) {
      hint = {
        template: explicitTemplate,
        requiredFields: explicitFields
      };
    }
    if (!hint || !group.hasImage) return;

    const imageGroupKey = group.baseProblemNumber;
    const source = {
      imageGroupKey,
      sourceGroupKey: group.baseProblemNumber,
      weakTypes: group.types,
      template: hint.template,
      requiredFields: hint.requiredFields,
      sourceProblemNumber: group.baseProblemNumber,
      sourceProblemText: String(group.sourceProblemText || '').slice(0, 3000),
      imageStructure: String(group.imageDescription || '').slice(0, 1200)
    };
    seenImageGroups[imageGroupKey] = source;
    imageSources.push(source);
  });

  const assignedNumbers = {};
  const selectedSources = imageSources.slice(0, totalCount);
  selectedSources.forEach(source => {
    const target = numbered.find(item => {
      return !assignedNumbers[item.number] && item.sourceGroupKey === source.sourceGroupKey;
    });
    if (!target) return;
    assignedNumbers[target.number] = source;
  });

  numbered.forEach(item => {
    const source = assignedNumbers[item.number];
    item.imageRequired = Boolean(source);
    if (item.imageRequired) {
      item.imageTemplate = source.template;
      item.imageRequiredFields = source.requiredFields;
      item.imageGroupKey = source.imageGroupKey;
      item.sourceProblemNumber = source.sourceProblemNumber;
      item.sourceProblemText = source.sourceProblemText;
      item.imageStructure = source.imageStructure;
    }
  });
  const effectiveImageCount = Object.keys(assignedNumbers).length;

  return {
    totalCount,
    noImageCount: totalCount - effectiveImageCount,
    imageCount: effectiveImageCount,
    weakTypes,
    sourceProblemCount: sourceGroups.length,
    sourceGroups: sourceGroups.map(group => ({
      baseProblemNumber: group.baseProblemNumber,
      sourceProblemNumbers: group.sourceProblemNumbers,
      subproblemCount: group.subproblemCount,
      types: group.types,
      formType: group.formType
    })),
    countByType: countByGroup,
    countBySourceGroup: countByGroup,
    items: numbered
  };
}

function distributeTwinCountsByWeakType_(sourceGroups, maxPerType) {
  const result = {};
  const groupsByType = {};
  (sourceGroups || []).forEach(group => {
    const groupKey = group.baseProblemNumber;
    const typeKey = getTwinGroupWeakTypeKey_(group);
    result[groupKey] = 0;
    if (!groupsByType[typeKey]) groupsByType[typeKey] = [];
    groupsByType[typeKey].push(group);
  });

  Object.keys(groupsByType).forEach(typeKey => {
    const groups = groupsByType[typeKey];
    const count = Math.max(1, Number(maxPerType || 1));
    for (let index = 0; index < count; index++) {
      const group = groups[index % groups.length];
      result[group.baseProblemNumber] += 1;
    }
  });
  return result;
}

function getTwinGroupWeakTypeKey_(group) {
  const types = group && group.types || [];
  return types.length ? types.slice().sort().join(' / ') : String(group && group.baseProblemNumber || '');
}

function groupTwinWrongProblems_(wrongProblems) {
  const grouped = {};
  (wrongProblems || []).forEach(problem => {
    const baseProblemNumber = String(
      problem.imageGroupKey
      || getBaseProblemNumber_(problem.problemNumber)
      || problem.sourceProblemNumber
      || problem.problemNumber
    );
    if (!grouped[baseProblemNumber]) {
      grouped[baseProblemNumber] = {
        baseProblemNumber,
        problems: [],
        sourceProblemNumbers: [],
        types: [],
        formTypes: [],
        answers: [],
        unit1: String(problem.unit1 || ''),
        unit2: String(problem.unit2 || ''),
        hasImage: false,
        imageTemplateHint: null,
        imageDescription: ''
      };
    }
    const group = grouped[baseProblemNumber];
    group.problems.push(problem);
    group.sourceProblemNumbers.push(problem.problemNumber);
    if (problem.type && group.types.indexOf(problem.type) < 0) group.types.push(problem.type);
    if (problem.formType && group.formTypes.indexOf(problem.formType) < 0) group.formTypes.push(problem.formType);
    if (problem.answer) {
      group.answers.push({
        problemNumber: problem.problemNumber,
        answer: problem.answer
      });
    }
    if (problem.hasImage) group.hasImage = true;
    if (!group.imageTemplateHint && problem.imageTemplateHint) {
      group.imageTemplateHint = problem.imageTemplateHint;
    }
    if (!group.imageDescription && problem.imageDescription) {
      group.imageDescription = problem.imageDescription;
    }
  });

  return Object.keys(grouped)
    .sort(compareProblemNumbers_)
    .map(key => {
      const group = grouped[key];
      group.sourceProblemNumbers = unique_(group.sourceProblemNumbers).sort(compareProblemNumbers_);
      group.subproblemCount = group.sourceProblemNumbers.filter(number => getSubProblemIndex_(number) > 0).length;
      if (!group.subproblemCount) group.subproblemCount = 1;
      const seenTexts = {};
      const textParts = [];
      group.problems
        .slice()
        .sort((a, b) => compareProblemNumbers_(a.problemNumber, b.problemNumber))
        .forEach(problem => {
          const text = String(problem.problemText || '').trim();
          if (!text || seenTexts[text]) return;
          seenTexts[text] = true;
          textParts.push(
            (group.subproblemCount > 1 ? '[' + problem.problemNumber + ']\n' : '') + text
          );
        });
      group.sourceProblemText = textParts.join('\n\n');
      group.formType = pickTwinGroupFormType_(group);
      return group;
    });
}

function pickTwinGroupFormType_(group) {
  const formTypes = group && group.formTypes || [];
  if (formTypes.indexOf('5지선다형') !== -1) return '5지선다형';
  if (formTypes.indexOf('서술형') !== -1) return '서술형';
  if (formTypes.indexOf('단답형') !== -1) return '단답형';
  return inferProblemFormType_(group && group.sourceProblemText || '') || '단답형';
}

function chunkTwinPlanItems_(items, maxSize) {
  const noImageItems = (items || []).filter(item => item.imageRequired !== true);
  const imageItems = (items || []).filter(item => item.imageRequired === true);
  return chunkByMaxSize_(noImageItems, maxSize).concat(chunkByMaxSize_(imageItems, maxSize));
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
      problem: String(item.problem || item.body || '').trim(),
      answer: String(item.answer || '').trim(),
      solution: String(item.solution || item.explanation || '').trim(),
      body: String(item.body || '').trim()
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
  if (isPerfectScoreMarker_(wrongNumbersText)) return [];
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
    return buildWrongProblemFromBankRow_(exactNumber, row);
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

function buildWrongProblemFromBankRow_(problemNumber, row) {
  const exactNumber = normalizeProblemNumber_(problemNumber);
  const problemText = String(row['문제본문'] || '').trim();
  return {
    problemNumber: exactNumber,
    sourceProblemNumber: normalizeProblemNumber_(row['문제번호']),
    imageGroupKey: getBaseProblemNumber_(exactNumber),
    rawType: String(row['문제 유형'] || '').trim(),
    type: String(row['표준 문제 유형'] || row['문제 유형'] || '').trim(),
    formType: normalizeProblemFormType_(row['문항형식']) || inferProblemFormType_(problemText),
    unit1: String(row['상위 단원'] || '').trim(),
    unit2: String(row['하위 단원'] || '').trim(),
    problemText,
    answer: String(row['정답'] || '').trim(),
    sourceLink: String(row['링크'] || '').trim(),
    hasImage: String(row['이미지포함여부'] || '').trim().toUpperCase() === 'TRUE',
    imageDescription: String(row['이미지설명'] || '').trim(),
    imageTemplateHint: String(row['이미지템플릿'] || '').trim()
      ? {
          template: String(row['이미지템플릿'] || '').trim(),
          requiredFields: String(row['이미지필수항목'] || '').trim()
        }
      : null
  };
}

function normalizeProblemFormType_(value) {
  const text = String(value || '').replace(/\s+/g, '');
  if (!text) return '';
  if (/5|오지|객관|선다|선택/.test(text)) return '5지선다형';
  if (/서술|논술|풀이과정|이유/.test(text)) return '서술형';
  if (/단답|주관/.test(text)) return '단답형';
  return '';
}

function inferProblemFormType_(problemText) {
  const text = String(problemText || '');
  const choiceCount = (text.match(/[①②③④⑤]/g) || []).length;
  if (choiceCount >= 4) return '5지선다형';
  if (/서술형|풀이\s*과정|이유를\s*서술|증명|과정을\s*쓰|채점기준/.test(text)) return '서술형';
  return text.trim() ? '단답형' : '';
}

function expandTwinSourceProblems_(examName, actualWrongProblems) {
  const selected = actualWrongProblems || [];
  const selectedBases = {};
  selected.forEach(problem => {
    selectedBases[getBaseProblemNumber_(problem.problemNumber)] = true;
  });

  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEETS.PROBLEM_BANK);
  const bankRows = readObjects_(sheet)
    .map(item => item.rowObject)
    .filter(row => String(row['시험지 이름']) === String(examName));
  const siblingProblems = [];
  bankRows.forEach(row => {
    const number = normalizeProblemNumber_(row['문제번호']);
    const baseNumber = getBaseProblemNumber_(number);
    if (!number || !selectedBases[baseNumber] || getSubProblemIndex_(number) <= 0) return;
    siblingProblems.push(buildWrongProblemFromBankRow_(number, row));
  });

  const byNumber = {};
  selected.concat(siblingProblems).forEach(problem => {
    const number = normalizeProblemNumber_(problem.problemNumber);
    if (!byNumber[number]) byNumber[number] = problem;
  });
  return Object.keys(byNumber)
    .sort(compareProblemNumbers_)
    .map(number => byNumber[number]);
}

function isPerfectScoreMarker_(value) {
  return String(value || '').replace(/\s+/g, '') === PERFECT_SCORE_MARKER.replace(/\s+/g, '');
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
      imageTemplate: String(row['이미지템플릿'] || '').trim(),
      imageRequiredFields: String(row['이미지필수항목'] || '').trim(),
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
  wrongProblems.forEach(problem => {
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
      newProblemsForSummary.push(problem);
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
  if (summary.recordCount > 0) return summary;
  return buildStudentHistorySummaryFromRaw_(studentName, currentWrongProblems);
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

  const monthlyUnitTypes = {};
  const repeatedTypes = {};
  const repeatedUnits = {};
  rows.forEach(row => {
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
    recordCount: rows.length,
    currentWeakTypes: Object.keys(currentKeys),
    monthlyUnitWeakTypeCounts: monthSummary,
    repeatedTypes: topCounts_(repeatedTypes, 10),
    repeatedUnits: topCounts_(repeatedUnits, 10)
  };
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
  const studentFolder = getStudentOutputFolder_(studentName);
  const file = createUniqueTextFile_(studentFolder, fileName, text);
  return file.getUrl();
}

function getStudentOutputFolder_(studentName) {
  const myDrive = DriveApp.getRootFolder();
  const studentsRoot = getOrCreateChildFolder_(myDrive, '학생폴더');
  return getOrCreateChildFolder_(studentsRoot, sanitizeFileName_(studentName));
}

function getDriveRootFolderId_() {
  const configs = readAdminConfigs_();
  const withFolder = configs.find(config => config.driveRootFolderId);
  return withFolder ? withFolder.driveRootFolderId : '';
}

function moveFileToStudentFolder_(file, studentName) {
  const studentFolder = getStudentOutputFolder_(studentName);
  file.moveTo(studentFolder);
  return studentFolder;
}

function enqueueTasks_(tasks) {
  if (!tasks.length) return 0;
  const ss = SpreadsheetApp.getActive();
  const now = new Date();
  let enqueuedCount = 0;
  [SHEETS.QUEUE, SHEETS.GENERATION_QUEUE].forEach(queueName => {
    const sheet = ensureSheet_(ss, queueName, HEADERS.QUEUE);
    const existingKeys = getOpenQueueKeys_(sheet);
    const uniqueTasks = tasks
      .filter(task => getQueueSheetNameForTask_(task.taskType) === queueName)
      .filter(task => {
        const key = buildQueueKey_(task.taskType, task.targetSheet, task.targetRow);
        if (existingKeys[key]) return false;
        existingKeys[key] = true;
        return true;
      });
    if (!uniqueTasks.length) return;

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
    enqueuedCount += uniqueTasks.length;
  });
  return enqueuedCount;
}

function getQueueSheetNameForTask_(taskType) {
  return isPaidGenerationTask_(taskType) ? SHEETS.GENERATION_QUEUE : SHEETS.QUEUE;
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
    const sheet = ss.getSheetByName(sheetName);
    const headers = getHeaderMap_(sheet);
    return Boolean(sheet.getRange(rowNumber, headers['분석 보고서']).getValue());
  }

  if (taskType === TASK_TYPES.SIMILAR_PROBLEMS) {
    const sheet = ss.getSheetByName(sheetName);
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

function normalizeProblemNumber_(value) {
  return String(value || '')
    .replace(/\s+/g, '')
    .replace(/[–—]/g, '-')
    .trim();
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
    const controlEscapedJsonText = escapeJsonStringControlCharacters_(jsonText);
    try {
      return JSON.parse(controlEscapedJsonText);
    } catch (secondErr) {
      const escapedJsonText = controlEscapedJsonText.replace(/\\(?!["\\/bfnrtu])/g, '\\\\');
      try {
        return JSON.parse(escapedJsonText);
      } catch (thirdErr) {
        const strippedJsonText = controlEscapedJsonText.replace(/\\(?!["\\/bfnrtu])/g, '');
        try {
          return JSON.parse(strippedJsonText);
        } catch (fourthErr) {
          throw new Error('AI 응답 JSON 파싱 실패: ' + fourthErr.message + ' / 원문 일부: ' + jsonText.slice(0, 500));
        }
      }
    }
  }
}

function escapeJsonStringControlCharacters_(text) {
  const source = String(text || '');
  let result = '';
  let inString = false;
  let escaped = false;

  for (let i = 0; i < source.length; i++) {
    const char = source.charAt(i);
    const code = source.charCodeAt(i);

    if (!inString) {
      result += char;
      if (char === '"') inString = true;
      continue;
    }
    if (escaped) {
      result += char;
      escaped = false;
      continue;
    }
    if (char === '\\') {
      result += char;
      escaped = true;
      continue;
    }
    if (char === '"') {
      result += char;
      inString = false;
      continue;
    }

    if (char === '\n') {
      result += '\\n';
    } else if (char === '\r') {
      result += '\\r';
    } else if (char === '\t') {
      result += '\\t';
    } else if (code < 32) {
      result += '\\u' + ('0000' + code.toString(16)).slice(-4);
    } else {
      result += char;
    }
  }
  return result;
}

function parseGeneratedProblemArray_(text, planItems) {
  const expectedNumbers = (planItems || []).map(item => Number(item.number));
  const delimited = parseGeneratedProblemDelimited_(text);
  if (delimited.length) {
    return remapGeneratedNumbersIfNeeded_(delimited, expectedNumbers)
      .map(normalizeGeneratedProblemImageSyntax_);
  }

  try {
    const parsed = parseJsonArray_(text);
    const normalized = parsed
      .map((item, index) => normalizeGeneratedProblemItem_(item, index + 1))
      .filter(item => item && item.number && (item.problem || item.body || item.answer || item.solution));
    return remapGeneratedNumbersIfNeeded_(normalized, expectedNumbers)
      .map(normalizeGeneratedProblemImageSyntax_);
  } catch (err) {
    const recovered = parseGeneratedProblemFallback_(text);
    if (recovered.length) {
      return remapGeneratedNumbersIfNeeded_(recovered, expectedNumbers)
        .map(normalizeGeneratedProblemImageSyntax_);
    }
    throw err;
  }
}

function normalizeGeneratedProblemImageSyntax_(item) {
  const normalized = Object.assign({}, item);
  ['problem', 'body'].forEach(key => {
    normalized[key] = normalizeImagePromptBlocks_(
      String(normalized[key] || '').replace(/\[그림\s*필요\s*:/g, '[이미지 필요:')
    );
  });
  return normalized;
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
  const fromPlainText = parseGeneratedProblemPlainText_(cleaned);
  if (fromPlainText.length) return fromPlainText;
  return parseGeneratedProblemLabelBlocks_(cleaned);
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

function parseGeneratedProblemLabelBlocks_(text) {
  const source = String(text || '').replace(/\r/g, '').trim();
  const regex = /(?:^|\n)\s*번호\s*:\s*(\d+)\s*(?=\n|$)/g;
  const matches = [];
  let match;
  while ((match = regex.exec(source)) !== null) {
    matches.push({
      number: Number(match[1]),
      index: match.index,
      contentStart: regex.lastIndex
    });
  }
  return matches.map((item, index) => {
    const next = matches[index + 1];
    const block = source.slice(item.contentStart, next ? next.index : source.length).trim();
    const parsed = parseGeneratedProblemBlock_('번호: ' + item.number + '\n' + block);
    return parsed;
  }).filter(Boolean);
}

function remapGeneratedNumbersIfNeeded_(items, expectedNumbers) {
  if (!items.length || !expectedNumbers || !expectedNumbers.length) return items;
  const expectedSet = {};
  expectedNumbers.forEach(number => expectedSet[Number(number)] = true);
  const usedExpected = {};
  items.forEach(item => {
    const number = Number(item.number);
    if (expectedSet[number] && !usedExpected[number]) usedExpected[number] = true;
  });
  const unusedExpected = expectedNumbers
    .map(Number)
    .filter(number => !usedExpected[number]);
  let unusedIndex = 0;
  return items.map((item, index) => {
    const currentNumber = Number(item.number);
    if (expectedSet[currentNumber]) return item;
    const remappedNumber = unusedExpected[unusedIndex++];
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
  return text
    .replace(/^(?:\uC815\uB2F5|\uD574\uC124|\uD480\uC774)\s*:\s*/, '')
    .trim();
}

function hasDraftLeakText_(item) {
  const problemText = String(item && item.problem || '');
  if (/(?:^|\n)\s*(?:정답|해설)\s*:/m.test(problemText)) return true;
  const solutionText = String(item && item.solution || '');
  if (/(?:^|\n)\s*(?:정답|해설)\s*:/m.test(solutionText)) return true;
  const text = [
    item && item.problem,
    item && item.answer,
    item && item.solution,
    item && item.body
  ].join('\n');
  return /(?:잠시만|다시\s*시도|다시\s*풀이|다시\s*(?:계산|확인|설정|작성)|문제\s*설정을?\s*(?:변경|수정)|숫자를?\s*(?:다시\s*)?조정|계산이\s*(?:깔끔하게|복잡)|해설에\s*이런\s*문구|역설계|내가\s*만든\s*문제|여전히\s*정수로|정수가\s*아니|정수가\s*아니네요|원래\s*오답|오답\s*목록을?\s*참고|참고하여.*(?:조정|변경)|수정된\s*최종|선택지를?\s*(?:다시\s*)?(?:확인|변경|수정)|기존\s*선택지|정답에\s*해당하는\s*선택지|죄송합니다|문제를?\s*(?:다시\s*)?(?:만들|바꾸|수정)|인수분해\s*가능한\s*숫자를?\s*찾)/.test(text);
}

function formatProblemOnly_(number, problem) {
  const cleaned = String(problem || '')
    .replace(/^[^\n.]{0,20}\d+\.\s*/, '')
    .replace(/^\s*\d+\.\s*/, '')
    .trim();
  const imageTags = [];
  const body = cleaned
    .replace(/\[\uC774\uBBF8\uC9C0 \uD544\uC694:[^\]]+\]/g, tag => {
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
  return scope.split(',').map(item => item.trim()).indexOf(String(currentSheetName).trim()) >= 0;
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
    [TASK_TYPES.PROBLEM_ANALYSIS, '*', 'A-project-01', '여기에_API_KEY', 10, 250000, 250, DEFAULT_MODEL, 2, 12000, 15000, 3000, '여기에_DRIVE_ROOT_FOLDER_ID', 'FALSE'],
    [TASK_TYPES.PROBLEM_ANALYSIS, '*', 'A-project-02', '여기에_API_KEY', 10, 250000, 250, DEFAULT_MODEL, 2, 12000, 15000, 3000, '여기에_DRIVE_ROOT_FOLDER_ID', 'FALSE'],
    [TASK_TYPES.STUDENT_REPORT, '원장님', 'B-project-01', '여기에_API_KEY', 10, 250000, 250, DEFAULT_MODEL, 2, 12000, 0, 5000, '여기에_DRIVE_ROOT_FOLDER_ID', 'FALSE'],
    [TASK_TYPES.SIMILAR_PROBLEMS, '원장님', 'C-project-01', '여기에_API_KEY', 10, 250000, 250, DEFAULT_MODEL, 2, 12000, 0, 9000, '여기에_DRIVE_ROOT_FOLDER_ID', 'FALSE'],
    [TASK_TYPES.SIMILAR_PROBLEMS, '원장님', 'C-project-02', '여기에_API_KEY', 10, 250000, 250, DEFAULT_MODEL, 2, 12000, 0, 9000, '여기에_DRIVE_ROOT_FOLDER_ID', 'FALSE']
  ]);
}

function seedTwinRuleExamples_(sheet) {
  if (sheet.getLastRow() > 1) return;
  sheet.getRange(2, 1, 2, HEADERS.TWIN_RULES.length).setValues([
    ['이차방정식의 근의 개수', 3, '중', '판별식을 사용해 근의 개수를 판단하는 문제를 만든다.', '원문 숫자와 완전히 같은 계수 사용 금지', '', '', 'TRUE', 'TRUE'],
    ['함수의 그래프 해석', 3, '중', '그래프의 교점, 증가/감소, y절편을 묻는 문제를 만든다.', '그림 없이 풀 수 없는 문항 금지', 'parabola_basic_shape', 'equation', 'TRUE', 'TRUE']
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


/**
 * === 중앙/선생님 파일 분리 운영용 override ===
 * 원본 엔진은 유지하고, 선생님별 원격 파일 요청/결과 쓰기만 덮어씁니다.
 */

TASK_TYPES.GENERAL_PROBLEMS = 'GENERAL_PROBLEMS';
TASK_TYPES.PAST_EXAM_PROBLEMS = 'PAST_EXAM_PROBLEMS';
TASK_TYPES.PAST_EXAM_ANALYSIS = 'PAST_EXAM_ANALYSIS';
SHEETS.GENERATION_BANK = '생성문제은행';
SHEETS.PAST_EXAM_BANK = '기출문제은행';
SHEETS.PAST_EXAM_REGISTRATION = '기출시험지등록';
HEADERS.ADMIN = ['기능', '적용시트', '프로젝트명', 'API키', 'RPM', 'TPM', 'RPD', '모델명', '1회처리개수', '요청간대기ms', 'Drive루트폴더ID', '사용여부', '이미지없는문제수', '이미지있는문제수'];
HEADERS.GENERATION_BANK = ['교재 이름', '상위단원', '하위단원', '문제유형번호', '유형명', '대표문항', '정답', '해설', '도형그래프포함여부', '이미지링크', '도형그래프설명', '도형그래프템플릿', '생성규칙', '금지사항', '신뢰도', '검산메모', '처리상태', '사용여부'];
HEADERS.PAST_EXAM_BANK = ['학교명', '연도', '학년', '학기', '시험구분', '문제번호', '문제본문', '정답', '해설', '상위단원', '하위단원', '문제유형', '난이도', '도형그래프포함여부', '이미지링크', '이미지설명', '이미지템플릿', '이미지필수항목', '기출이미지ID', '원본PDF링크', '신뢰도', '검산메모', '처리상태', '사용여부'];
HEADERS.PAST_EXAM_REGISTRATION = ['학교명', '연도', '학년', '학기', '시험구분', '통합PDF링크', '별도정답해설PDF링크', '처리상태', '오류메시지', '등록문항수', '마지막처리시간'];

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
    .addItem('생성문제은행 시트 생성/정비', 'setupGenerationBankSheet')
    .addItem('기출 시트 생성/정비', 'setupPastExamSheets')
    .addItem('기출 PDF 분석 작업 등록', 'enqueuePastExamAnalysisTasks')
    .addSeparator()
    .addItem('문제은행 분석 작업 등록', 'enqueueProblemAnalysisTasks')
    .addItem('현재 시트 보고서 작업 등록', 'enqueueStudentReportTasks')
    .addItem('현재 시트 쌍둥이 문항 작업 등록', 'enqueueSimilarProblemTasks')
    .addSeparator()
    .addItem('무료 작업큐 1회 처리', 'processQueue')
    .addItem('유료 문항생성큐 1회 처리', 'processGenerationQueue')
    .addItem('기존 유료 작업 문항생성큐로 이동', 'migratePaidTasksToGenerationQueue')
    .addItem('실패 문항생성 작업 재개', 'resumeFailedGenerationTasks')
    .addItem('RUNNING 작업 즉시 복구', 'forceRecoverRunningTasks')
    .addItem('무료+유료 트리거 전체 설치', 'installQueueTrigger')
    .addItem('무료+유료 트리거 전체 삭제', 'removeQueueTriggers')
    .addToUi();
}

function createUniqueTextFile_(folder, requestedName, text) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const uniqueName = getUniqueFileName_(folder, requestedName);
    return folder.createFile(uniqueName, text, MimeType.PLAIN_TEXT);
  } finally {
    lock.releaseLock();
  }
}

function renameFileUniquely_(file, requestedName) {
  const parents = file.getParents();
  if (!parents.hasNext()) {
    file.setName(sanitizeFileName_(requestedName));
    return file.getName();
  }

  const folder = parents.next();
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const uniqueName = getUniqueFileName_(folder, requestedName, file.getId());
    file.setName(uniqueName);
    return uniqueName;
  } finally {
    lock.releaseLock();
  }
}

function getUniqueFileName_(folder, requestedName, excludedFileId) {
  const safeName = sanitizeFileName_(requestedName);
  const dotIndex = safeName.lastIndexOf('.');
  const hasExtension = dotIndex > 0;
  const baseName = hasExtension ? safeName.slice(0, dotIndex) : safeName;
  const extension = hasExtension ? safeName.slice(dotIndex) : '';
  let candidate = safeName;
  let suffix = 1;

  while (folderHasNamedFile_(folder, candidate, excludedFileId)) {
    candidate = baseName + ' (' + suffix + ')' + extension;
    suffix += 1;
  }
  return candidate;
}

function folderHasNamedFile_(folder, fileName, excludedFileId) {
  const files = folder.getFilesByName(fileName);
  while (files.hasNext()) {
    if (files.next().getId() !== String(excludedFileId || '')) return true;
  }
  return false;
}

function forceRecoverRunningTasks() {
  const ss = SpreadsheetApp.getActive();
  let recovered = 0;
  [SHEETS.QUEUE, SHEETS.GENERATION_QUEUE].forEach(queueName => {
    const queueSheet = ss.getSheetByName(queueName);
    if (!queueSheet) return;
    const queueHeaders = getHeaderMap_(queueSheet);
    readObjects_(queueSheet).forEach(item => {
      if (item.rowObject['상태'] !== QUEUE_STATUS.RUNNING) return;
      setRowValues_(queueSheet, item.rowNumber, queueHeaders, {
        '상태': QUEUE_STATUS.PENDING,
        '예약시각': new Date(),
        '처리시간': new Date(),
        '오류메시지': '제한시간 초과 작업을 수동 복구했습니다.'
      });
      recovered += 1;
    });
  });
  SpreadsheetApp.getUi().alert('RUNNING 작업 ' + recovered + '개를 PENDING으로 복구했습니다.');
  return recovered;
}

function resumeFailedGenerationTasks() {
  const ss = SpreadsheetApp.getActive();
  const queueSheet = ss.getSheetByName(SHEETS.GENERATION_QUEUE);
  if (!queueSheet) throw new Error('문항생성큐 시트가 없습니다.');
  const queueHeaders = getHeaderMap_(queueSheet);
  let resumed = 0;

  readObjects_(queueSheet).forEach(item => {
    const row = item.rowObject;
    if (row['상태'] !== QUEUE_STATUS.FAILED) return;
    if (!isPaidGenerationTask_(row['작업종류'])) return;

    setRowValues_(queueSheet, item.rowNumber, queueHeaders, {
      '상태': QUEUE_STATUS.PENDING,
      '재시도횟수': 0,
      '예약시각': new Date(),
      '오류메시지': '기존 진행 파일에서 자동 재개 대기',
      '처리시간': new Date()
    });
    tryMarkTeacherTaskRetry_(
      row['대상시트'],
      Number(row['대상행']),
      row['페이로드JSON'],
      new Error('기존 진행 지점부터 재개합니다.')
    );
    resumed += 1;
  });

  SpreadsheetApp.getUi().alert(
    resumed
      ? '실패한 문항생성 작업 ' + resumed + '개를 기존 진행 지점부터 재개합니다.'
      : '재개할 실패 문항생성 작업이 없습니다.'
  );
  return resumed;
}

function migratePaidTasksToGenerationQueue() {
  const ss = SpreadsheetApp.getActive();
  const source = ensureSheet_(ss, SHEETS.QUEUE, HEADERS.QUEUE);
  const target = ensureSheet_(ss, SHEETS.GENERATION_QUEUE, HEADERS.QUEUE);
  const targetOpenKeys = getOpenQueueKeys_(target);
  const rowsToDelete = [];
  const valuesToAppend = [];

  readObjects_(source).forEach(item => {
    const row = item.rowObject;
    if (!isPaidGenerationTask_(row['작업종류'])) return;
    if ([QUEUE_STATUS.PENDING, QUEUE_STATUS.RUNNING].indexOf(row['상태']) < 0) return;
    const key = buildQueueKey_(row['작업종류'], row['대상시트'], row['대상행']);
    if (!targetOpenKeys[key]) {
      valuesToAppend.push(HEADERS.QUEUE.map(header => {
        if (header === '상태') return QUEUE_STATUS.PENDING;
        if (header === '예약시각') return new Date();
        if (header === '오류메시지') return '작업큐에서 문항생성큐로 이동했습니다.';
        return row[header] === undefined ? '' : row[header];
      }));
      targetOpenKeys[key] = true;
    }
    rowsToDelete.push(item.rowNumber);
  });

  if (valuesToAppend.length) {
    target.getRange(target.getLastRow() + 1, 1, valuesToAppend.length, HEADERS.QUEUE.length)
      .setValues(valuesToAppend);
  }
  rowsToDelete.sort((a, b) => b - a).forEach(rowNumber => source.deleteRow(rowNumber));
  SpreadsheetApp.getUi().alert(
    '유료 생성 작업 ' + rowsToDelete.length + '개를 문항생성큐로 이동했습니다.'
  );
  return rowsToDelete.length;
}

function setupGenerationBankSheet() {
  const sheet = ensureSheet_(SpreadsheetApp.getActive(), SHEETS.GENERATION_BANK, HEADERS.GENERATION_BANK);
  ensureBankReviewWorkflow_(sheet);
}

function setupPastExamBankSheet() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, SHEETS.PAST_EXAM_BANK, HEADERS.PAST_EXAM_BANK);
  ensureBankReviewWorkflow_(sheet);
}

function ensureBankReviewWorkflow_(sheet) {
  renameHeaderIfNeeded_(sheet, '검토메모', '검산메모');
  ensureHeaderBefore_(sheet, '신뢰도', '사용여부');
  ensureHeaderAfter_(sheet, '검산메모', '신뢰도');
  ensureHeaderAfter_(sheet, '처리상태', '검산메모');

  const headers = getHeaderMap_(sheet);
  const statusColumn = headers['처리상태'];
  if (statusColumn) {
    sheet.getRange(2, statusColumn, Math.max(sheet.getMaxRows() - 1, 1), 1)
      .clearDataValidations();
  }

  readObjects_(sheet).forEach(item => {
    if (String(item.rowObject['처리상태'] || '').trim()) return;
    setRowValues_(sheet, item.rowNumber, headers, {
      '처리상태': 'REVIEW'
    });
  });
}

function renameHeaderIfNeeded_(sheet, oldHeader, newHeader) {
  const width = Math.max(sheet.getLastColumn(), 1);
  const headers = sheet.getRange(1, 1, 1, width).getValues()[0].map(value => String(value).trim());
  const oldIndex = headers.indexOf(oldHeader);
  const newIndex = headers.indexOf(newHeader);
  if (oldIndex >= 0 && newIndex < 0) {
    sheet.getRange(1, oldIndex + 1).setValue(newHeader);
  }
}

function ensureHeaderBefore_(sheet, header, beforeHeader) {
  const width = Math.max(sheet.getLastColumn(), 1);
  const headers = sheet.getRange(1, 1, 1, width).getValues()[0].map(value => String(value).trim());
  const headerIndex = headers.indexOf(header);
  const beforeIndex = headers.indexOf(beforeHeader);
  if (beforeIndex < 0) {
    if (headerIndex >= 0) return;
    sheet.getRange(1, width + 1).setValue(header);
    return;
  }
  if (headerIndex === beforeIndex - 1) return;

  sheet.insertColumnBefore(beforeIndex + 1);
  const destinationColumn = beforeIndex + 1;
  if (headerIndex < 0) {
    sheet.getRange(1, destinationColumn).setValue(header);
    return;
  }

  const shiftedSourceIndex = headerIndex >= beforeIndex ? headerIndex + 1 : headerIndex;
  sheet.getRange(1, shiftedSourceIndex + 1, sheet.getMaxRows(), 1)
    .copyTo(sheet.getRange(1, destinationColumn, sheet.getMaxRows(), 1));
  sheet.deleteColumn(shiftedSourceIndex + 1);
}

function ensureHeaderAfter_(sheet, header, afterHeader) {
  const width = Math.max(sheet.getLastColumn(), 1);
  const headers = sheet.getRange(1, 1, 1, width).getValues()[0].map(value => String(value).trim());
  const headerIndex = headers.indexOf(header);
  const afterIndex = headers.indexOf(afterHeader);
  if (afterIndex < 0) {
    if (headerIndex >= 0) return;
    sheet.getRange(1, width + 1).setValue(header);
    return;
  }
  if (headerIndex === afterIndex + 1) return;

  sheet.insertColumnAfter(afterIndex + 1);
  const destinationColumn = afterIndex + 2;
  if (headerIndex < 0) {
    sheet.getRange(1, destinationColumn).setValue(header);
    return;
  }

  const shiftedSourceIndex = headerIndex > afterIndex ? headerIndex + 1 : headerIndex;
  sheet.getRange(1, shiftedSourceIndex + 1, sheet.getMaxRows(), 1)
    .copyTo(sheet.getRange(1, destinationColumn, sheet.getMaxRows(), 1));
  sheet.deleteColumn(shiftedSourceIndex + 1);
}

function setupPastExamRegistrationSheet() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, SHEETS.PAST_EXAM_REGISTRATION, HEADERS.PAST_EXAM_REGISTRATION);
  ensureHeaderIncludes_(sheet, HEADERS.PAST_EXAM_REGISTRATION);
}

function setupPastExamSheets() {
  setupPastExamBankSheet();
  setupPastExamRegistrationSheet();
  SpreadsheetApp.getUi().alert('기출시험지등록과 기출문제은행 시트를 생성/정비했습니다.');
}

function enqueuePastExamAnalysisTasks() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ss.getSheetByName(SHEETS.PAST_EXAM_REGISTRATION);
  if (!sheet) throw new Error('기출시험지등록 시트가 없습니다. setupPastExamRegistrationSheet()를 먼저 실행하세요.');

  const rows = readObjects_(sheet);
  const tasks = rows
    .filter(item => {
      const row = item.rowObject;
      return row['학교명']
        && row['연도']
        && row['학년']
        && row['학기']
        && row['시험구분']
        && row['통합PDF링크'];
    })
    .filter(item => String(item.rowObject['처리상태'] || '').toUpperCase() !== 'DONE')
    .map(item => ({
      taskType: TASK_TYPES.PAST_EXAM_ANALYSIS,
      targetSheet: SHEETS.PAST_EXAM_REGISTRATION,
      targetRow: item.rowNumber,
      payload: {
        registrationRow: item.rowNumber,
        schoolName: String(item.rowObject['학교명'] || '').trim(),
        year: normalizeYear_(item.rowObject['연도']),
        grade: String(item.rowObject['학년'] || '').trim(),
        semester: String(item.rowObject['학기'] || '').trim(),
        examType: String(item.rowObject['시험구분'] || '').trim(),
        examPdfUrl: String(item.rowObject['통합PDF링크'] || '').trim(),
        answerPdfUrl: String(item.rowObject['별도정답해설PDF링크'] || '').trim()
      }
    }));

  const count = enqueueTasks_(tasks);
  SpreadsheetApp.getUi().alert(count + '개의 기출 PDF 분석 작업을 등록했습니다.');
  return count;
}

function doPost(e) {
  try {
    const payload = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    if (payload.action === 'GET_PROBLEM_NUMBERS') {
      return ContentService
        .createTextOutput(JSON.stringify({
          ok: true,
          problemNumbers: getProblemNumbersForExam_(payload.examName)
        }))
        .setMimeType(ContentService.MimeType.JSON);
    }

    const taskId = enqueueTeacherRequestFromWebApp_(payload);
    return ContentService
      .createTextOutput(JSON.stringify({ ok: true, taskId }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: String(err && err.message ? err.message : err) }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function doGet(e) {
  return ContentService
    .createTextOutput(JSON.stringify({
      ok: true,
      app: 'wrong-note-master',
      message: 'central master web app is reachable',
      time: new Date().toISOString()
    }))
    .setMimeType(ContentService.MimeType.JSON);
}

function enqueueTeacherRequestFromWebApp_(payload) {
  const normalized = normalizeTeacherWebPayload_(payload);
  const ss = SpreadsheetApp.getActive();
  const queueName = getQueueSheetNameForTask_(normalized.taskType);
  const queueSheet = ss.getSheetByName(queueName);
  if (!queueSheet) throw new Error(queueName + ' 시트가 없습니다. setupSheets()를 먼저 실행하세요.');

  const task = {
    taskType: normalized.taskType,
    targetSheet: buildRemoteTeacherTargetSheet_(normalized),
    targetRow: normalized.teacherRow,
    payload: normalized
  };

  const existingTaskId = findOpenRemoteTeacherQueueTask_(queueSheet, task);
  if (existingTaskId) return existingTaskId;

  const now = new Date();
  const values = [[
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
  ]];
  queueSheet.getRange(queueSheet.getLastRow() + 1, 1, 1, HEADERS.QUEUE.length).setValues(values);
  return values[0][0];
}

function normalizeTeacherWebPayload_(payload) {
  if (!payload) throw new Error('요청 payload가 비어 있습니다.');
  const taskType = normalizeRemoteTaskType_(payload.taskType);
  const normalized = {
    taskType,
    teacherId: String(payload.teacherId || '').trim(),
    teacherFileId: String(payload.teacherFileId || '').trim(),
    teacherSheetName: String(payload.teacherSheetName || '').trim(),
    teacherRow: Number(payload.teacherRow || 0),
    studentName: String(payload.studentName || '').trim(),
    examName: String(payload.examName || '').trim(),
    wrongNumbersText: String(payload.wrongNumbersText || payload.wrongNumbers || '').trim(),
    requestedAt: payload.requestedAt || new Date().toISOString()
  };

  if (!normalized.teacherFileId) throw new Error('teacherFileId가 비어 있습니다.');
  if (!normalized.teacherSheetName) throw new Error('teacherSheetName이 비어 있습니다.');
  if (!normalized.teacherRow) throw new Error('teacherRow가 비어 있습니다.');
  if (!normalized.studentName) throw new Error('학생 이름이 비어 있습니다.');
  if (!normalized.examName) throw new Error('시험지 이름이 비어 있습니다.');
  if (!normalized.wrongNumbersText) throw new Error('틀린 문제 번호가 비어 있습니다.');
  return normalized;
}

function normalizeRemoteTaskType_(taskType) {
  const text = String(taskType || '').trim();
  if (text === 'CUMULATIVE_REPORT') return TASK_TYPES.STUDENT_REPORT;
  if (text === TASK_TYPES.STUDENT_REPORT
      || text === TASK_TYPES.SIMILAR_PROBLEMS
      || text === TASK_TYPES.GENERAL_PROBLEMS
      || text === TASK_TYPES.PAST_EXAM_PROBLEMS) return text;
  throw new Error('선생님 파일에서 요청할 수 없는 작업종류입니다: ' + text);
}

function buildRemoteTeacherTargetSheet_(payload) {
  return ['REMOTE', payload.teacherFileId, payload.teacherSheetName].join('::');
}

function findOpenRemoteTeacherQueueTask_(queueSheet, task) {
  const targetKey = [task.taskType, task.payload.teacherFileId, task.payload.teacherSheetName, task.targetRow].join('||');
  const rows = readObjects_(queueSheet);
  for (let i = 0; i < rows.length; i += 1) {
    const row = rows[i].rowObject;
    if (row['상태'] !== QUEUE_STATUS.PENDING && row['상태'] !== QUEUE_STATUS.RUNNING) continue;
    let payload = {};
    try {
      payload = JSON.parse(row['페이로드JSON'] || '{}');
    } catch (err) {
      payload = {};
    }
    const rowKey = [row['작업종류'], payload.teacherFileId, payload.teacherSheetName, row['대상행']].join('||');
    if (rowKey === targetKey) return row['작업ID'];
  }
  return '';
}

function getTeacherSheetForTask_(targetSheetName, payload) {
  if (payload && payload.teacherFileId) {
    return SpreadsheetApp.openById(payload.teacherFileId).getSheetByName(payload.teacherSheetName);
  }
  return SpreadsheetApp.getActive().getSheetByName(targetSheetName);
}

function getTeacherScopeForTask_(targetSheetName, payload) {
  return (payload && payload.teacherId) || targetSheetName || '*';
}

function getProblemNumbersForExam_(examName) {
  const name = String(examName || '').trim();
  if (!name) throw new Error('시험지 이름이 비어 있습니다.');

  const ss = SpreadsheetApp.getActive();
  const sheet = ss.getSheetByName(SHEETS.PROBLEM_BANK);
  if (!sheet) throw new Error('문제은행 시트가 없습니다.');

  const rows = readObjects_(sheet);
  const numbers = rows
    .filter(item => String(item.rowObject['시험지 이름'] || '').trim() === name)
    .map(item => normalizeProblemNumber_(item.rowObject['문제번호']))
    .filter(Boolean);

  return unique_(numbers).sort(compareProblemNumbers_);
}

function pickAvailableKey_(feature, sheetScope, estimatedTokens) {
  const configs = pickAvailableKeys_(feature, sheetScope, estimatedTokens);
  if (!configs.length) return null;
  if (feature === TASK_TYPES.SIMILAR_PROBLEMS) return configs[0];
  return pickRoundRobinConfig_(feature, sheetScope, configs);
}

function defaultAttachmentTokenBudget_(feature) {
  if (feature === TASK_TYPES.PROBLEM_ANALYSIS) return 15000;
  if (feature === TASK_TYPES.PAST_EXAM_ANALYSIS) return 20000;
  return 0;
}

function defaultOutputTokenBudget_(feature) {
  if (feature === TASK_TYPES.PROBLEM_ANALYSIS) return 3000;
  if (feature === TASK_TYPES.STUDENT_REPORT) return 5000;
  if (feature === TASK_TYPES.SIMILAR_PROBLEMS) return 9000;
  if (feature === TASK_TYPES.GENERAL_PROBLEMS) return 9000;
  if (feature === TASK_TYPES.PAST_EXAM_PROBLEMS) return 9000;
  if (feature === TASK_TYPES.PAST_EXAM_ANALYSIS) return 8000;
  return 0;
}

function readAdminConfigs_() {
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEETS.ADMIN);
  return readObjects_(sheet)
    .map(item => item.rowObject)
    .filter(row => row['기능'] && row['프로젝트명'] && row['API키'])
    .map(row => {
      const feature = normalizeFeatureName_(row['기능']);
      return {
        feature,
        sheetScope: String(row['적용시트'] || '').trim(),
        projectName: String(row['프로젝트명']).trim(),
        apiKey: String(row['API키']).trim(),
        rpm: Number(row['RPM'] || 10),
        tpm: Number(row['TPM'] || 250000),
        rpd: Number(row['RPD'] || 250),
        model: String(row['모델명'] || DEFAULT_MODEL).trim(),
        batchSize: Number(row['1회처리개수'] || DEFAULT_BATCH_SIZE),
        delayMs: Number(row['요청간대기ms'] || DEFAULT_REQUEST_DELAY_MS),
        attachmentTokenBudget: defaultAttachmentTokenBudget_(feature),
        outputTokenBudget: defaultOutputTokenBudget_(feature),
        driveRootFolderId: String(row['Drive루트폴더ID'] || '').trim(),
        twinNoImageCount: Number(row['이미지없는문제수'] || 0),
        twinImageCount: Number(row['이미지있는문제수'] || 0),
        enabled: String(row['사용여부'] || 'TRUE').toUpperCase() !== 'FALSE'
      };
    });
}

function seedAdminExamples_(sheet) {
  if (sheet.getLastRow() > 1) return;
  sheet.getRange(2, 1, 6, HEADERS.ADMIN.length).setValues([
    [TASK_TYPES.PROBLEM_ANALYSIS, '*', 'A-project-01', '여기에_API_KEY', 10, 250000, 250, DEFAULT_MODEL, 2, 12000, '여기에_DRIVE_ROOT_FOLDER_ID', 'FALSE', '', ''],
    [TASK_TYPES.STUDENT_REPORT, '*', 'B-project-01', '여기에_API_KEY', 10, 250000, 250, DEFAULT_MODEL, 2, 12000, '여기에_DRIVE_ROOT_FOLDER_ID', 'FALSE', '', ''],
    [TASK_TYPES.GENERAL_PROBLEMS, '*', 'C-project-01', '여기에_API_KEY', 10, 250000, 250, DEFAULT_MODEL, 2, 12000, '여기에_DRIVE_ROOT_FOLDER_ID', 'FALSE', '', ''],
    [TASK_TYPES.SIMILAR_PROBLEMS, '*', 'D-paid-01', '여기에_API_KEY', 1000, 0, 0, DEFAULT_MODEL, 3, 0, '여기에_DRIVE_ROOT_FOLDER_ID', 'FALSE', '', ''],
    [TASK_TYPES.PAST_EXAM_PROBLEMS, '*', 'E-paid-01', '여기에_API_KEY', 1000, 0, 0, DEFAULT_MODEL, 3, 0, '여기에_DRIVE_ROOT_FOLDER_ID', 'FALSE', '', ''],
    [TASK_TYPES.PAST_EXAM_ANALYSIS, '*', 'F-project-01', '여기에_API_KEY', 10, 250000, 250, DEFAULT_MODEL, 1, 12000, '여기에_DRIVE_ROOT_FOLDER_ID', 'FALSE', '', '']
  ]);
}

function seedTwinImageCountDefaults_(sheet) {
  // 쌍둥이문항은 오답 원문의 고유 이미지 수를 자동 사용한다.
}

function isTeacherTask_(taskType) {
  return taskType === TASK_TYPES.STUDENT_REPORT
    || taskType === TASK_TYPES.SIMILAR_PROBLEMS
    || taskType === TASK_TYPES.GENERAL_PROBLEMS
    || taskType === TASK_TYPES.PAST_EXAM_PROBLEMS;
}

function processQueueItem_(queueSheet, item) {
  const queueHeaders = getHeaderMap_(queueSheet);
  const taskType = item.rowObject['작업종류'];
  const retryCount = Number(item.rowObject['재시도횟수'] || 0);
  let payload = {};

  setRowValues_(queueSheet, item.rowNumber, queueHeaders, {
    '상태': QUEUE_STATUS.RUNNING,
    '처리시간': new Date(),
    '오류메시지': ''
  });

  try {
    payload = JSON.parse(item.rowObject['페이로드JSON'] || '{}');
    let taskResult = null;
    if (shouldSkipCompletedTask_(item.rowObject, payload)) {
      setRowValues_(queueSheet, item.rowNumber, queueHeaders, {
        '상태': QUEUE_STATUS.DONE,
        '처리시간': new Date(),
        '오류메시지': '이미 결과 저장 칸이 채워져 있어 스킵했습니다.'
      });
      return;
    }

    if (taskType === TASK_TYPES.PROBLEM_ANALYSIS) {
      handleProblemAnalysis_(payload);
    } else if (taskType === TASK_TYPES.STUDENT_REPORT) {
      handleStudentReport_(item.rowObject['대상시트'], Number(item.rowObject['대상행']), payload);
    } else if (taskType === TASK_TYPES.SIMILAR_PROBLEMS) {
      taskResult = handleSimilarProblems_(
        item.rowObject['대상시트'],
        Number(item.rowObject['대상행']),
        payload
      );
    } else if (taskType === TASK_TYPES.GENERAL_PROBLEMS) {
      taskResult = handleGeneralProblems_(
        item.rowObject['대상시트'],
        Number(item.rowObject['대상행']),
        payload
      );
    } else if (taskType === TASK_TYPES.PAST_EXAM_PROBLEMS) {
      taskResult = handlePastExamProblems_(
        item.rowObject['대상시트'],
        Number(item.rowObject['대상행']),
        payload
      );
    } else if (taskType === TASK_TYPES.PAST_EXAM_ANALYSIS) {
      handlePastExamAnalysis_(payload);
    } else {
      throw new Error('알 수 없는 작업종류: ' + taskType);
    }

    if (taskResult && taskResult.continueLater) {
      setRowValues_(queueSheet, item.rowNumber, queueHeaders, {
        '상태': QUEUE_STATUS.PENDING,
        '예약시각': new Date(Date.now() + 15 * 1000),
        '처리시간': new Date(),
        '오류메시지': taskResult.message || '분할 작업 진행 중',
        '페이로드JSON': JSON.stringify(taskResult.payload || payload)
      });
      return;
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
    if (err && err.resumePayload) {
      payload = Object.assign({}, payload, err.resumePayload);
    }
    if (err && err.deferOnly) {
      setRowValues_(queueSheet, item.rowNumber, queueHeaders, {
        '상태': QUEUE_STATUS.PENDING,
        '예약시각': new Date(Date.now() + (err.deferMs || 60 * 1000)),
        '처리시간': new Date(),
        '오류메시지': String(err.message || err).slice(0, 1000),
        '페이로드JSON': JSON.stringify(payload)
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
      '오류메시지': String(err && err.message ? err.message : err).slice(0, 1000),
      '페이로드JSON': JSON.stringify(payload)
    });
    if (nextStatus === QUEUE_STATUS.FAILED) {
      tryMarkTeacherTaskError_(item.rowObject['대상시트'], Number(item.rowObject['대상행']), item.rowObject['페이로드JSON'], err);
    } else {
      tryMarkTeacherTaskRetry_(
        item.rowObject['대상시트'],
        Number(item.rowObject['대상행']),
        item.rowObject['페이로드JSON'],
        err
      );
    }
  }
}

function shouldSkipCompletedTask_(queueRow, payload) {
  const taskType = queueRow['작업종류'];
  if (taskType === TASK_TYPES.PROBLEM_ANALYSIS) return false;
  if (!isTeacherTask_(taskType)) return false;

  const sheet = getTeacherSheetForTask_(queueRow['대상시트'], payload);
  if (!sheet) return false;
  const rowNumber = Number(queueRow['대상행']);
  const rowObject = readRowObject_(sheet, rowNumber);
  if (taskType === TASK_TYPES.STUDENT_REPORT) return Boolean(rowObject['분석 보고서']);
  if (taskType === TASK_TYPES.SIMILAR_PROBLEMS) return Boolean(rowObject['쌍둥이 문항']);
  if (taskType === TASK_TYPES.GENERAL_PROBLEMS) return Boolean(rowObject['생성결과']);
  if (taskType === TASK_TYPES.PAST_EXAM_PROBLEMS) return Boolean(rowObject['생성결과']);
  return false;
}

function handleStudentReport_(targetSheetName, targetRow, payload) {
  const teacherSheet = getTeacherSheetForTask_(targetSheetName, payload);
  if (!teacherSheet) throw new Error('선생님 시트를 찾을 수 없습니다: ' + targetSheetName);
  const teacherHeaders = getHeaderMap_(teacherSheet);
  const wrongProblems = lookupWrongProblems_(payload.examName, payload.wrongNumbersText);
  const historySummary = buildStudentHistorySummary_(payload.studentName, wrongProblems);

  const prompt = buildReportPrompt_(payload.studentName, payload.examName, wrongProblems, historySummary);
  const response = callGemini_(TASK_TYPES.STUDENT_REPORT, getTeacherScopeForTask_(targetSheetName, payload), prompt, []);
  const fileUrl = saveTextToStudentFolder_(
    payload.studentName,
    sanitizeFileName_(payload.examName + ' 보고서.txt'),
    response.text
  );
  upsertWrongHistory_(getTeacherScopeForTask_(targetSheetName, payload), targetRow, payload.studentName, payload.examName, wrongProblems, {
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
  const teacherSheet = getTeacherSheetForTask_(targetSheetName, payload);
  if (!teacherSheet) throw new Error('선생님 시트를 찾을 수 없습니다: ' + targetSheetName);
  const teacherHeaders = getHeaderMap_(teacherSheet);
  const rowObject = readRowObject_(teacherSheet, targetRow);
  const actualWrongProblems = lookupWrongProblems_(payload.examName, payload.wrongNumbersText);
  if (!actualWrongProblems.length) {
    throw new Error('틀린 문제가 없는 100점 기록에는 쌍둥이문항을 생성하지 않습니다.');
  }
  const twinSourceProblems = expandTwinSourceProblems_(payload.examName, actualWrongProblems);
  const rulesByType = readTwinRules_();
  const missingTypes = unique_(twinSourceProblems.map(item => item.type).filter(type => !rulesByType[type]));
  if (missingTypes.length) {
    throw new Error('쌍둥이_규칙 시트에 규칙이 없는 문제 유형: ' + missingTypes.join(', '));
  }

  const reportText = readDriveTextFromUrl_(rowObject['분석 보고서']);
  const teacherScope = getTeacherScopeForTask_(targetSheetName, payload);
  const plan = payload.twinPlan && payload.twinPlan.items
    ? payload.twinPlan
    : buildTwinGenerationPlan_(twinSourceProblems, teacherScope, rulesByType);
  const progress = getGenerationProgressFile_(
    targetSheetName,
    payload,
    'twinProgressFileId',
    '쌍둥이문항'
  );
  const existingGeneratedProblems = readJsonArrayFromFile_(progress.file);
  const existingNumbers = {};
  existingGeneratedProblems.forEach(item => {
    existingNumbers[Number(item.number)] = true;
  });
  const chunkItems = plan.items
    .filter(item => !existingNumbers[Number(item.number)])
    .slice(0, 30);
  const chunkPlan = Object.assign({}, plan, {
    totalCount: chunkItems.length,
    items: chunkItems
  });
  let generatedChunk;
  try {
    generatedChunk = generateSimilarProblemsWithPool_(
      teacherScope,
      payload.studentName,
      payload.examName,
      twinSourceProblems,
      reportText,
      rulesByType,
      chunkPlan
    );
  } catch (error) {
    const savedProblems = mergeGeneratedProblemsByNumber_(
      existingGeneratedProblems,
      error.partialGeneratedProblems || []
    );
    progress.file.setContent(JSON.stringify(savedProblems));
    error.resumePayload = {
      twinPlan: plan,
      twinProgressFileId: progress.file.getId(),
      twinGeneratedCount: savedProblems.length
    };
    setRowValues_(teacherSheet, targetRow, teacherHeaders, {
      '처리상태': 'RUNNING ' + savedProblems.length + '/' + plan.items.length,
      '오류메시지': '성공한 ' + savedProblems.length + '문항은 보존했습니다. 실패 묶음만 재시도합니다.'
    });
    throw error;
  }
  const generatedProblems = mergeGeneratedProblemsByNumber_(
    existingGeneratedProblems,
    generatedChunk
  );
  progress.file.setContent(JSON.stringify(generatedProblems));
  const nextCount = generatedProblems.length;

  if (nextCount < plan.items.length) {
    setRowValues_(teacherSheet, targetRow, teacherHeaders, {
      '처리상태': 'RUNNING ' + nextCount + '/' + plan.items.length,
      '오류메시지': ''
    });
    return {
      continueLater: true,
      payload: Object.assign({}, payload, {
        twinPlan: plan,
        twinProgressFileId: progress.file.getId(),
        twinGeneratedCount: nextCount
      }),
      message: '쌍둥이문항 분할 생성 진행 중: ' + nextCount + '/' + plan.items.length
    };
  }

  const finalText = formatGeneratedProblems_(payload.studentName, payload.examName, plan, generatedProblems);
  moveFileToStudentFolder_(progress.file, payload.studentName);
  renameFileUniquely_(
    progress.file,
    payload.studentName + '_' + payload.examName + '_쌍둥이문항.txt'
  );
  progress.file.setContent(finalText);
  const fileUrl = progress.file.getUrl();
  upsertWrongHistory_(teacherScope, targetRow, payload.studentName, payload.examName, actualWrongProblems, {
    twinUrl: fileUrl
  });

  setRowValues_(teacherSheet, targetRow, teacherHeaders, {
    '쌍둥이 문항': fileUrl,
    '처리상태': hasGeneratedProblemReviewItems_(generatedProblems) ? 'REVIEW' : 'DONE',
    '오류메시지': hasGeneratedProblemReviewItems_(generatedProblems) ? '쌍둥이 문항 일부에 [검토 필요] 표시가 있습니다.' : ''
  });
  return { continueLater: false };
}

function mergeGeneratedProblemsByNumber_(existing, additions) {
  const byNumber = {};
  (existing || []).concat(additions || []).forEach(item => {
    const number = Number(item && item.number);
    if (number) byNumber[number] = item;
  });
  return Object.keys(byNumber)
    .map(Number)
    .sort((a, b) => a - b)
    .map(number => byNumber[number]);
}

function handleGeneralProblems_(targetSheetName, targetRow, payload) {
  const teacherSheet = getTeacherSheetForTask_(targetSheetName, payload);
  if (!teacherSheet) throw new Error('선생님 시트를 찾을 수 없습니다: ' + targetSheetName);
  const teacherHeaders = getHeaderMap_(teacherSheet);
  const requestItems = getGeneralProblemRequestItems_(payload);
  const totalCount = requestItems.reduce((sum, item) => sum + Number(item.count || 0), 0);
  if (totalCount > 30) throw new Error('일반문항 생성은 한 번에 최대 30문제까지 가능합니다.');
  if (totalCount < 1) throw new Error('생성할 문항수가 없습니다.');

  const allExampleGroups = requestItems.map(item => ({
    item,
    examples: lookupGenerationBankItems_(item)
  }));
  const missing = allExampleGroups.filter(group => !group.examples.length);
  if (missing.length) {
    throw new Error('생성문제은행에서 대표문항을 찾지 못한 조건: ' + missing.map(group => [
      group.item.bookName,
      group.item.unit1,
      group.item.unit2,
      '유형' + group.item.typeNumber
    ].join(' / ')).join(', '));
  }

  const progress = getGenerationProgressFile_(
    targetSheetName,
    payload,
    'generalProgressFileId',
    '생성문제'
  );
  const completedNumbers = getCompletedProgressNumbers_(progress.file);
  const generatedCount = Object.keys(completedNumbers).length;
  const chunkGroups = [];
  let searchNumber = 1;
  while (searchNumber <= totalCount && chunkGroups.length < 6) {
    while (searchNumber <= totalCount && completedNumbers[searchNumber]) searchNumber += 1;
    if (searchNumber > totalCount) break;
    let chunkCountLimit = 0;
    while (chunkCountLimit < 5
        && searchNumber + chunkCountLimit <= totalCount
        && !completedNumbers[searchNumber + chunkCountLimit]) {
      chunkCountLimit += 1;
    }
    const chunkItems = sliceGeneralProblemRequestItems_(requestItems, searchNumber - 1, chunkCountLimit);
    const chunkCount = chunkItems.reduce((sum, item) => sum + Number(item.count || 0), 0);
    if (!chunkCount) break;
    const exampleGroups = chunkItems.map(item => ({
      item,
      examples: lookupGenerationBankItems_(item)
    }));
    const chunkPayload = Object.assign({}, payload, {
      items: chunkItems,
      generalStartNumber: searchNumber
    });
    chunkGroups.push({
      count: chunkCount,
      startNumber: searchNumber,
      exampleGroups,
      prompt: buildGeneralProblemsPrompt_(chunkPayload, exampleGroups)
    });
    searchNumber += chunkCount;
  }

  const scope = getTeacherScopeForTask_(targetSheetName, payload);
  let responses;
  try {
    responses = callPaidGenerationBatch_(
      TASK_TYPES.GENERAL_PROBLEMS,
      scope,
      chunkGroups.map(group => group.prompt)
    );
  } catch (error) {
    savePartialGeneralProblemResponses_(progress.file, error.partialResults, chunkGroups);
    const savedCount = countProgressChunkItems_(progress.file);
    error.resumePayload = {
      generalProgressFileId: progress.file.getId(),
      generalGeneratedCount: savedCount
    };
    const rows = payload.targetRows && payload.targetRows.length ? payload.targetRows : [targetRow];
    rows.forEach(row => {
      setRowValues_(teacherSheet, Number(row), teacherHeaders, {
        '처리상태': 'RUNNING ' + savedCount + '/' + totalCount,
        '오류메시지': '성공한 ' + savedCount + '문항은 보존했습니다. 실패 묶음만 재시도합니다.'
      });
    });
    throw error;
  }
  responses.forEach((response, index) => {
    const requestIndex = Number.isFinite(response.requestIndex) ? response.requestIndex : index;
    const group = chunkGroups[requestIndex];
    const generatedText = validateGeneralProblemImageOutput_(response.text, group.exampleGroups);
    appendProgressTextChunk_(progress.file, generatedText, group.count, group.startNumber);
  });
  const nextCount = countProgressChunkItems_(progress.file);

  const rows = payload.targetRows && payload.targetRows.length ? payload.targetRows : [targetRow];
  if (nextCount < totalCount) {
    rows.forEach(row => {
      setRowValues_(teacherSheet, Number(row), teacherHeaders, {
        '처리상태': 'RUNNING ' + nextCount + '/' + totalCount,
        '오류메시지': ''
      });
    });
    return {
      continueLater: true,
      payload: Object.assign({}, payload, {
        generalProgressFileId: progress.file.getId(),
        generalGeneratedCount: nextCount
      }),
      message: '일반문항 분할 생성 진행 중: ' + nextCount + '/' + totalCount
    };
  }

  const finalText = formatGeneralProblemsResult_(payload, readProgressText_(progress.file));
  renameFileUniquely_(progress.file, buildGeneralProblemsFileName_(targetSheetName, payload));
  progress.file.setContent(finalText);
  const fileUrl = progress.file.getUrl();
  rows.forEach(row => {
    setRowValues_(teacherSheet, Number(row), teacherHeaders, {
      '생성결과': fileUrl,
      '처리상태': 'DONE',
      '오류메시지': ''
    });
  });
  return { continueLater: false };
}

function savePartialGeneralProblemResponses_(file, responses, chunkGroups) {
  (responses || []).forEach((response, index) => {
    const requestIndex = Number.isFinite(response.requestIndex) ? response.requestIndex : index;
    const group = chunkGroups[requestIndex];
    if (!group) return;
    try {
      const generatedText = validateGeneralProblemImageOutput_(response.text, group.exampleGroups);
      appendProgressTextChunk_(file, generatedText, group.count, group.startNumber);
    } catch (error) {
      // Invalid output is not checkpointed; only this group will be requested again.
    }
  });
}

function sliceGeneralProblemRequestItems_(items, completedCount, maxCount) {
  let skip = Math.max(0, Number(completedCount || 0));
  let remaining = Math.max(1, Number(maxCount || 5));
  const result = [];
  (items || []).forEach(item => {
    const itemCount = Number(item.count || 0);
    if (remaining <= 0 || itemCount <= 0) return;
    if (skip >= itemCount) {
      skip -= itemCount;
      return;
    }
    const available = itemCount - skip;
    const take = Math.min(available, remaining);
    result.push(Object.assign({}, item, { count: take }));
    remaining -= take;
    skip = 0;
  });
  return result;
}

function buildGeneralProblemsFileName_(targetSheetName, payload) {
  const ownerName = getGeneralProblemsOwnerName_(targetSheetName, payload);
  const dateText = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyyMMdd');
  return sanitizeFileName_(ownerName + '_' + dateText + '_생성문제.txt');
}

function callPaidGenerationBatch_(feature, sheetScope, prompts) {
  if (!prompts || !prompts.length) return [];
  const maxEstimatedTokens = Math.max.apply(null, prompts.map(prompt => estimateTokens_(prompt)));
  const keyConfig = pickAvailableKey_(feature, sheetScope, maxEstimatedTokens);
  if (!keyConfig) {
    throwDefer_(feature + ' 기능에 사용 가능한 유료 API 키가 없습니다.');
  }
  const requests = prompts.map(prompt => {
    if (!isWithinQuota_(keyConfig, estimateRequestTokens_(prompt, [], keyConfig))) {
      throwDefer_(keyConfig.projectName + ' 프로젝트 quota가 부족하여 다음 트리거로 연기합니다.');
    }
    return { keyConfig, prompt, extraParts: [] };
  });
  return callGeminiBatch_(feature, requests);
}

function saveGeneralProblemsText_(targetSheetName, payload, text) {
  const ownerName = getGeneralProblemsOwnerName_(targetSheetName, payload);
  const dateText = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyyMMdd');
  const fileName = sanitizeFileName_(ownerName + '_' + dateText + '_생성문제.txt');
  return saveTextToNamedFolder_(ownerName, fileName, text);
}

function getGeneralProblemsOwnerName_(targetSheetName, payload) {
  const studentName = String(payload.studentName || '').trim();
  if (studentName) return studentName;
  const teacherName = String(payload.teacherId || '').trim();
  if (teacherName) return teacherName;
  return String(targetSheetName || '선생님').replace(/^REMOTE::[^:]+::/, '') || '선생님';
}

function saveTextToNamedFolder_(folderName, fileName, text) {
  const rootFolderId = getDriveRootFolderId_();
  if (!rootFolderId) throw new Error('관리자_설정에 Drive루트폴더ID가 없습니다.');
  const root = DriveApp.getFolderById(rootFolderId);
  const folder = getOrCreateChildFolder_(root, sanitizeFileName_(folderName));
  const file = createUniqueTextFile_(folder, fileName, text);
  return file.getUrl();
}

function getGeneralProblemRequestItems_(payload) {
  if (payload.items && payload.items.length) return payload.items;
  return [{
    bookName: payload.bookName,
    unit1: payload.unit1,
    unit2: payload.unit2,
    typeNumber: payload.typeNumber,
    count: payload.count
  }];
}

function lookupGenerationBankItems_(payload) {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, SHEETS.GENERATION_BANK, HEADERS.GENERATION_BANK);
  ensureBankReviewWorkflow_(sheet);
  const bookName = String(payload.bookName || '').trim();
  const unit1 = String(payload.unit1 || '').trim();
  const unit2 = String(payload.unit2 || '').trim();
  const typeNumber = String(payload.typeNumber || '').trim();

  return readObjects_(sheet)
    .map(item => item.rowObject)
    .filter(row => String(row['사용여부'] || 'TRUE').toUpperCase() !== 'FALSE')
    .filter(row => String(row['교재 이름'] || '').trim() === bookName)
    .filter(row => String(row['상위단원'] || '').trim() === unit1)
    .filter(row => String(row['하위단원'] || '').trim() === unit2)
    .filter(row => String(row['문제유형번호'] || '').trim() === typeNumber);
}

function buildGeneralProblemsPrompt_(payload, exampleGroups) {
  const totalCount = exampleGroups.reduce((sum, group) => sum + Number(group.item.count || 0), 0);
  const startNumber = Math.max(1, Number(payload.generalStartNumber || 1));
  const exampleText = exampleGroups.map((group, groupIndex) => {
    const item = group.item;
    const templateHint = findExistingImageTemplateForGroup_(group);
    const examples = group.examples.map((example, index) => [
      '[대표문항 ' + (index + 1) + ']',
      '유형명: ' + String(example['유형명'] || ''),
      '대표문항: ' + String(example['대표문항'] || ''),
      '정답: ' + String(example['정답'] || ''),
      '해설: ' + String(example['해설'] || ''),
      '도형/그래프 포함: ' + String(example['도형그래프포함여부'] || ''),
      '이미지링크: ' + String(example['이미지링크'] || ''),
      '도형그래프설명: ' + String(example['도형그래프설명'] || ''),
      '도형그래프템플릿: ' + formatImageTemplateForPrompt_(example['도형그래프템플릿']),
      '생성규칙: ' + String(example['생성규칙'] || ''),
      '금지사항: ' + String(example['금지사항'] || '')
    ].join('\n')).join('\n\n');

    return [
      '[생성조건 ' + (groupIndex + 1) + ']',
      '교재 이름: ' + item.bookName,
      '상위단원: ' + item.unit1,
      '하위단원: ' + item.unit2,
      '문제유형번호: ' + item.typeNumber,
      '생성 문항수: ' + item.count,
      '기존 템플릿 검색결과: ' + formatExistingTemplateHint_(templateHint),
      examples
    ].join('\n');
  }).join('\n\n');

  return [
    '너는 중고등부 수학 문항을 만드는 수학 교사다.',
    '아래 생성문제은행의 대표문항을 참고해 같은 유형의 새 문제를 생성하라.',
    '중요 규칙:',
    getStandardProblemNumberingPromptRules_().join('\n'),
    '- 대표문항을 그대로 복사하지 말고 숫자, 조건, 문장을 바꿔라.',
    '- 같은 유형의 풀이 구조는 유지하되, 정답이 대표문항과 같아지지 않게 하라.',
    '- 도형이나 그래프가 필요한 문제는 반드시 [이미지 필요: ...] 형식으로 이미지 설명을 넣어라.',
    '- [그림 필요: ...] 표현은 절대 쓰지 말고, 반드시 [이미지 필요: ...]만 사용하라.',
    '- 도형/그래프의 변의 길이, 각도, 좌표, 계수 등 숫자는 대표문항과 다르게 바꿔라.',
    '- 도형/그래프가 포함된 유형이면 생성 문제에도 같은 종류의 도형/그래프가 포함되어야 한다.',
    '- 도형그래프템플릿에 type=geometry 또는 type=coordinate_plane이 있으면 도형 구조와 고정 키를 그대로 유지하라.',
    '- 템플릿의 {중괄호 변수}에는 새 문제와 일치하는 새 수치만 넣어라. 대표문항과 같은 수치를 쓰지 마라.',
    '- 이미지 문항은 [이미지 필요: ...] 바로 다음에 아래 IMAGE_PROMPT 블록을 반드시 출력하라.',
    '- IMAGE_PROMPT에는 설명 문장을 쓰지 말고 key=value 형식만 사용하라.',
    '- 기존 IMAGE_PROMPT 템플릿이 있는 유형은 type/shape를 새로 만들지 말고 template=... 형식을 우선 사용하라.',
    '- 각 생성조건의 기존 템플릿 검색결과가 있으면 해당 template 이름과 필수 항목을 반드시 사용하라.',
    '- rectangle_square_similar_split은 width, height, square_side에 렌더링 비율용 실제 숫자를 넣고, 문제에 문자 길이가 제시되면 width_label, height_label에 인쇄할 표기(예: 6, x)를 반드시 따로 넣어라. 근삿값 소수를 길이 라벨로 인쇄하지 마라.',
    '- 검색결과가 없더라도 아래 구현 템플릿 목록을 먼저 검토하라. 문제 구조가 정확히 일치하고 필요한 값을 모두 채울 수 있을 때만 사용하라.',
    '- 구현 템플릿 목록: ' + getImplementedImageTemplateNames_().join(', '),
    '- 매개변수가 포함된 세 일차방정식이 삼각형을 만들고 여러 매개변수 값의 경우를 그릴 때는 template=linear_parameter_triangle_cases, equations, parameter, parameter_values를 사용하라.',
    '- 직사각형 땅의 십자형 도로 문제는 template=rectangle_cross_road를 사용하고 width, height, road_width를 넣어라. 구해야 하는 도로 폭은 road_width=x로 쓴다.',
    '- geometry 필수 키: type=geometry, shape, coordinates. 필요하면 segments, center, radius를 사용하라.',
    '- coordinate_plane 필수 키: type=coordinate_plane과 equation 또는 points. 여러 식은 equation 값 안에서 세미콜론으로 구분하고, 필요하면 x_range, y_range, intersections, vertex, labels를 사용하라.',
    '- 이미지가 없는 유형에는 [이미지 필요]와 IMAGE_PROMPT를 출력하지 마라.',
    '- LaTeX, 마크다운 표, 코드블록은 쓰지 말고 일반 텍스트로 작성하라.',
    '- 문제에 별도의 <보기>가 필요한 경우 반드시 <보기>와 </보기> 태그로 감싸라. HWP 생성기는 이 블록을 1x1 표로 변환한다.',
    '- <보기> 안에는 보기 내용만 넣고 ①~⑤ 선택지는 <보기> 밖에 작성하라.',
    '- 각 문항은 문제, 정답, 해설을 포함하라.',
    '- 해설은 핵심 식과 결론만 최대 6줄로 작성하라. 문제 재진술, 단계 번호, 반복 계산, 검산 과정은 쓰지 마라.',
    '- 5지선다형과 단답형은 가능하면 2~4줄로 끝내라.',
    '- 문제나 숫자를 수정한 과정, 실패한 계산, 대안 문제, 사과문은 절대 출력하지 마라.',
    '- 생성조건별 요청 문항수를 정확히 지켜라.',
    '- 문항번호는 이번 요청 데이터의 시작번호부터 순서대로 매겨라.',
    '- 각 문항 앞에 교재/단원/유형번호를 표시하라.',
    '',
    '출력 형식:',
    '문항N.',
    '문제: ...',
    '[이미지 필요: 새 수치가 반영된 도형 또는 그래프 설명]',
    '[IMAGE_PROMPT:',
    'type=geometry',
    'shape=right_triangle',
    'coordinates=A(0,3),B(0,0),C(4,0)',
    'segments=AB,BC,CA',
    ']',
    '정답: ...',
    '해설: ...',
    '',
    '이번 요청 데이터:',
    '대상: ' + (String(payload.studentName || '').trim() || String(payload.teacherId || '').trim() || '선생님'),
    '총 생성 문항수: ' + totalCount,
    '이번 묶음 문항번호: ' + startNumber + '번부터 ' + (startNumber + totalCount - 1) + '번',
    '',
    '대표문항 자료:',
    exampleText
  ].join('\n');
}

function findExistingImageTemplateForGroup_(group) {
  const item = group.item || {};
  const source = [
    item.bookName,
    item.unit1,
    item.unit2,
    item.typeNumber
  ].concat((group.examples || []).reduce((parts, example) => parts.concat([
    example['유형명'],
    example['대표문항'],
    example['도형그래프설명'],
    example['생성규칙']
  ]), [])).join(' ');
  return findExistingImageTemplate_(source);
}

function findExistingImageTemplate_(value) {
  const text = String(value || '').replace(/\s+/g, '');
  if (!text) return null;

  if (/(?:두|2)(?:개의?)?(?:일차함수|직선)/.test(text)
      && /정사각형ABCD/.test(text)
      && /x축/.test(text)
      && /(?:점D.*점C|D.*C)/.test(text)) {
    return {
      template: 'linear_two_lines_xaxis_square',
      requiredFields: 'equation_left, equation_right'
    };
  }

  if (/(?:두직선|직선.*직선)/.test(text)
      && /(?:점A|A\(a,b\))/.test(text)
      && /(?:점B|B\(s,t\))/.test(text)
      && /(?:교점.*C|C\(m,n\))/.test(text)) {
    return {
      template: 'linear_two_lines_labeled_points',
      requiredFields: 'equation1, equation2, point_a_x, point_b_x'
    };
  }

  if (/(?:수직선|y축에평행|x좌표)/.test(text)
      && /(?:x\s*=|x축과만나|좌표평면)/.test(text)
      && !/(?:삼각형|넓이|포물선|이차함수)/.test(text)) {
    return {
      template: 'linear_vertical_line_position',
      requiredFields: 'x_value'
    };
  }

  if (/(?:정오각형|정다각형|오각형)/.test(text)
      && /(?:이어붙|이어진|공유|연결)/.test(text)
      && /(?:첫번째|두번째|세번째|과정|화살표)/.test(text)) {
    return {
      template: 'regular_polygon_chain_sequence',
      requiredFields: 'sides, side, stage_counts'
    };
  }

  if (/(?:직선|일차함수)/.test(text)
      && /사분면/.test(text)
      && /(?:왼쪽위.*오른쪽아래|기울기.*음수|기울기가음수)/.test(text)) {
    return {
      template: 'linear_sign_diagram',
      requiredFields: 'slope_sign, y_intercept_sign'
    };
  }

  if (/표/.test(text)
      && /(?:줄넘기|배드민턴|활동)/.test(text)
      && /(?:10분|열량|kcal)/i.test(text)) {
    return {
      template: 'activity_calorie_table',
      requiredFields: 'activities, calories_per_10min'
    };
  }

  if (/(?:두|2)/.test(text)
      && /(?:이차함수|포물선)/.test(text)
      && /원점/.test(text)
      && /평행사변형/.test(text)) {
    return {
      template: 'two_origin_parabolas_parallelogram',
      requiredFields: 'equation1, equation2, vertical_x'
    };
  }
  if (/(?:두|2)(?:개의?)?(?:이차함수|포물선)/.test(text)
      && /원점/.test(text)
      && /y축에평행/.test(text)
      && /(?:점P.*점Q.*점R|P.*Q.*R)/.test(text)
      && /(?:길이의비|PQ.*QR|선분PQ.*선분QR)/.test(text)) {
    return {
      template: 'two_origin_parabolas_vertical_line_ratio',
      requiredFields: 'equation1, equation2, vertical_x'
    };
  }
  if (/(?:이차함수|포물선)/.test(text)
      && /y축/.test(text)
      && /평행사변형/.test(text)) {
    return {
      template: 'parabola_yaxis_xpositive_parallelogram',
      requiredFields: 'equation, y_axis_y'
    };
  }
  if (/(?:두|2)(?:개의?)?(?:이차함수|포물선)/.test(text)
      && /정사각형/.test(text)
      && /(?:x축|y축).*평행/.test(text)
      && /(?:점A.*점C|A.*C)/.test(text)) {
    return {
      template: 'two_parabolas_axis_aligned_square',
      requiredFields: 'equation_left, equation_right, square_side'
    };
  }
  if (/(?:두|2)(?:개의?)?(?:이차함수|포물선)/.test(text)
      && /(?:서로의꼭짓점|각각의꼭짓점|꼭짓점을지난)/.test(text)) {
    return {
      template: 'two_parabolas_shared_vertex_intersections',
      requiredFields: 'equation1, equation2'
    };
  }
  if (/(?:이차함수|포물선).*(?:정사각형).*(?:좌표|내접)|(?:정사각형).*(?:이차함수|포물선).*(?:좌표|내접)/.test(text)) {
    return {
      template: 'parabola_inscribed_square',
      requiredFields: 'equation, x_left, x_right, y_bottom'
    };
  }
  if (/(?:평행사변형).*(?:좌표|넓이)|(?:좌표).*(?:평행사변형).*(?:넓이)/.test(text)) {
    return {
      template: 'coordinate_parallelogram',
      requiredFields: 'points'
    };
  }
  if (/직사각형/.test(text)
      && /(?:점P|P는|P가)/.test(text)
      && /(?:B에서C|BC를따라|선분BC)/.test(text)
      && /사다리꼴/.test(text)
      && /(?:AP|사다리꼴APCD|매초|속력|이동)/.test(text)) {
    return {
      template: 'moving_point_rectangle_trapezoid',
      requiredFields: 'rectangle_width, rectangle_height, point_speed'
    };
  }
  if (/(?:합동|같은|두|2).*(?:직각삼각형).*(?:이동|포개|겹치)|(?:직각삼각형).*(?:이동|포개|겹치).*(?:넓이|부분)/.test(text)) {
    return {
      template: 'sliding_right_triangles_overlap',
      requiredFields: 'base, height, speed'
    };
  }
  if (/(?:도형|직사각형|삼각형).*(?:넓이).*(?:변화|시간|움직)|(?:움직|시간).*(?:도형|직사각형|삼각형).*(?:넓이)/.test(text)) {
    return {
      template: 'moving_points_rectangle_triangle',
      requiredFields: 'rectangle_width, rectangle_height, point_p_speed, point_q_speed'
    };
  }
  if (/직각삼각형/.test(text)
      && /(?:점P.*점Q|P.*Q)/.test(text)
      && /(?:매초|속도|늘어|줄어|이동)/.test(text)
      && /(?:AB|수직)/.test(text)
      && /(?:BC|수평)/.test(text)) {
    return {
      template: 'moving_points_right_triangle',
      requiredFields: 'vertical_leg, horizontal_leg, point_p_speed, point_q_speed'
    };
  }
  if (/직사각형/.test(text)
      && /정사각형/.test(text)
      && /(?:잘라|잘라내|떼어|제거)/.test(text)
      && /닮음/.test(text)) {
    return {
      template: 'rectangle_square_similar_split',
      requiredFields: 'width, height, square_side'
    };
  }

  if (/(?:세|3개의?)반원/.test(text)) {
    return {
      template: 'three_semicircles',
      requiredFields: 'diameter, split (둘 다 렌더링용 실제 숫자)'
    };
  }
  if (/(?:큰원|원O|원의내부|원내부)/.test(text)
      && /(?:두|2)(?:개의?)?반원/.test(text)
      && /지름/.test(text)) {
    return {
      template: 'circle_with_two_semicircles',
      requiredFields: 'outer_diameter, left_inner_diameter, right_inner_diameter'
    };
  }
  if (/(?:사분원).*(?:삼각비|sin|cos|tan|직각삼각형)/i.test(text)) {
    return {
      template: 'unit_quarter_circle_trig',
      requiredFields: 'angle'
    };
  }
  if (/(?:세|3개의?)일차방정식.*삼각형.*(?:넓이|매개변수)|(?:매개변수|미지수).*세일차방정식.*삼각형/.test(text)) {
    return {
      template: 'linear_parameter_triangle_cases',
      requiredFields: 'equations, parameter, parameter_values'
    };
  }

  const roadWord = '(?:도로|통로|길(?!이))';
  const roadPattern = new RegExp(
    roadWord + '.*(?:직사각형|밭|땅|화단)|(?:직사각형|밭|땅|화단).*' + roadWord
  );
  if (roadPattern.test(text)) {
    const slanted = /(?:비스듬|기울|대각|평행사변형|사선)/.test(text);
    const multipleRoadPattern = new RegExp(
      '(?:3개|세개|여러개|평행).*' + roadWord
      + '|' + roadWord + '.*(?:3개|세개|여러개|평행)'
      + '|\\(\\s*\\d+\\s*-\\s*2x\\s*\\).*\\(\\s*\\d+\\s*-\\s*x\\s*\\)'
    );
    const multipleParallel = multipleRoadPattern.test(text);
    return {
      template: multipleParallel
        ? 'rectangle_parallel_roads'
        : (slanted ? 'rectangle_slanted_cross_road' : 'rectangle_cross_road'),
      requiredFields: multipleParallel
        ? 'width, height, road_width, vertical_road_count, horizontal_road_count'
        : 'width, height, road_width'
    };
  }
  if (/(?:일차함수|직선).*(?:이차함수|포물선)|(?:이차함수|포물선).*(?:일차함수|직선)/.test(text)) {
    return { template: 'line_to_parabola_quadrant_match', requiredFields: 'line_equation, parabola_form' };
  }
  if (/(?:이등변삼각형).*(?:이등분선)|(?:이등분선).*(?:이등변삼각형)/.test(text)) {
    return { template: 'isosceles_triangle_bisector', requiredFields: 'base, equal_side, base_angle' };
  }
  if (/(?:두|2).*(?:이차함수|포물선).*(?:사다리꼴)|(?:사다리꼴).*(?:두|2).*(?:이차함수|포물선)/.test(text)) {
    return {
      template: 'two_parabolas_vertical_trapezoid',
      requiredFields: 'equation_top, equation_bottom, x_left, x_right'
    };
  }
  if (/(?:이차함수|포물선)/.test(text)
      && /x축/.test(text)
      && /(?:점A.*점B|A.*B)/.test(text)
      && /(?:꼭짓점은?C|꼭짓점C)/.test(text)) {
    return {
      template: 'parabola_xintercepts_vertex_triangle',
      requiredFields: 'equation'
    };
  }
  if (/(?:이차함수|포물선)/.test(text)
      && /x축/.test(text)
      && /(?:두점|두개의점|두교점|점A.*점B|A.*B)/.test(text)
      && /(?:원점|O)/.test(text)
      && /(?:음의x축.*양의x축|A.*O.*B|A와B사이)/.test(text)) {
    return {
      template: 'parabola_labeled_xintercepts',
      requiredFields: 'equation, curve_label'
    };
  }
  if (/(?:이차함수|포물선).*(?:꼭짓점|y절편|x절편)/.test(text)
      && !/(?:5개|다섯개|보기|고르)/.test(text)) {
    return {
      template: 'parabola_basic_shape',
      requiredFields: 'equation'
    };
  }

  const rules = [
    [/(?:공원|산책로|테두리|둘레길|가장자리).*(?:직사각형|가로|세로)|(?:직사각형|가로|세로).*(?:공원|산책로|테두리|둘레길|가장자리)/,
      'rectangular_park_border', 'inner_width, inner_height, border_width'],
    [/(?:정사각형|직사각형).*(?:종이|귀퉁이|상자|전개도|잘라).*부피|(?:상자|전개도|귀퉁이).*(?:정사각형|직사각형|종이)/,
      /직사각형/.test(text) ? 'open_box_net_rectangular_paper' : 'open_box_net_equal_cuts',
      /직사각형/.test(text) ? 'paper_width, paper_height, cut_side' : 'paper_side, cut_side'],
    [/(?:두|2)(?:개의?)?정사각형|선분.*(?:양쪽|각각).*정사각형|정사각형.*두개|정사각형.*넓이의합/,
      'two_squares_on_segment', 'total_length'],
    [/(?:반지름).*(?:늘|증가).*(?:원|넓이)|(?:원|넓이).*(?:반지름).*(?:늘|증가)/,
      'annulus_radius_increase', 'inner_radius, increase'],
    [/(?:동심원|원띠|고리).*(?:반지름|넓이|색칠)|(?:중심이같은|중심이동일한).*(?:두|2).*원/,
      'annulus_area', 'outer_radius, inner_radius'],
    [/(?:보기|고르|알맞은|바르게|그래프).*(?:아래로볼록|위로볼록|두근|x절편|개형|사분면)|(?:아래로볼록|위로볼록|두근|x절편|개형|사분면).*(?:보기|고르|알맞은|바르게|그래프)/,
      'multiple_choice_parabola_position', 'choices'],
    [/(?:x축|x절편|근).*(?:꼭짓점|정점).*(?:삼각형|넓이)|(?:꼭짓점|정점).*(?:x축|x절편|근).*(?:삼각형|넓이)/,
      'parabola_xintercepts_vertex_triangle', 'equation'],
    [/(?:x축|x절편).*(?:y축|y절편).*(?:삼각형|넓이)|(?:y축|y절편).*(?:x축|x절편).*(?:삼각형|넓이)/,
      'parabola_xintercepts_yintercept_triangle', 'equation'],
    [/(?:y축|y절편).*(?:꼭짓점|정점).*(?:원점|x축|삼각형|넓이)|(?:꼭짓점|정점).*(?:y축|y절편).*(?:원점|x축|삼각형|넓이)/,
      'parabola_yintercept_vertex_xintercept_triangle', 'equation, x_intercept'],
    [/(?:두|2).*이차함수.*(?:사이|렌즈|잎사귀|둘러싸).*넓이/,
      'two_parabolas_between_area', 'equation1, equation2'],
    [/(?:두|2).*이차함수.*(?:수직선|x=).*(?:둘러싸|넓이)|(?:수직선|x=).*(?:두|2).*이차함수.*(?:둘러싸|넓이)/,
      'parabola_band_area', 'equation_top, equation_bottom, x_left, x_right'],
    [/(?:원점|O).*(?:여러|계수|a).*이차함수|y=ax|포물선.*계수.*비교/,
      'parabola_family_origin', 'equations, curve_labels'],
    [/(?:직선|일차함수).*(?:x절편|y절편|축과만나는점)|(?:x절편|y절편).*(?:직선|일차함수)/,
      'linear_basic_intercepts', 'equation'],
    [/(?:직선|일차함수).*(?:보기|고르|알맞은|바르게)|(?:보기|고르|알맞은|바르게).*(?:직선|일차함수)/,
      'multiple_choice_linear_position', 'choices']
  ];

  for (let i = 0; i < rules.length; i++) {
    if (rules[i][0].test(text)) {
      return { template: rules[i][1], requiredFields: rules[i][2] };
    }
  }
  return null;
}

function getImageTemplateSourceCompatibilityError_(template, value) {
  const text = String(value || '').replace(/\s+/g, '');
  const hasRoadWord = /도로|통로|길(?!이)/.test(text);
  const checks = {
    rectangle_cross_road: hasRoadWord && /직사각형|밭|땅|화단/.test(text),
    rectangle_slanted_cross_road: hasRoadWord && /직사각형|밭|땅|화단/.test(text),
    rectangle_parallel_roads: hasRoadWord && /직사각형|밭|땅|화단/.test(text),
    open_box_net_rectangular_paper: /직사각형/.test(text)
      && /종이/.test(text)
      && /귀퉁이/.test(text)
      && /정사각형/.test(text)
      && /(?:잘라|잘라내|잘리|잘려|제거)/.test(text)
      && /상자/.test(text),
    open_box_net_equal_cuts: /종이/.test(text)
      && /귀퉁이/.test(text)
      && /정사각형/.test(text)
      && /(?:잘라|잘라내|잘리|잘려|제거)/.test(text)
      && /상자/.test(text),
    rectangle_square_similar_split: /직사각형/.test(text)
      && /정사각형/.test(text)
      && /(?:잘라|잘라내|떼어|제거)/.test(text)
      && /닮음/.test(text),
    two_parabolas_shared_vertex_intersections: /(?:두|2)(?:개의?)?(?:이차함수|포물선)/.test(text)
      && /(?:서로의꼭짓점|각각의꼭짓점|꼭짓점을지난)/.test(text),
    two_origin_parabolas_parallelogram: /(?:두|2)/.test(text)
      && /(?:이차함수|포물선)/.test(text)
      && /원점/.test(text)
      && /평행사변형/.test(text),
    two_origin_parabolas_vertical_line_ratio: /(?:두|2)(?:개의?)?(?:이차함수|포물선)/.test(text)
      && /원점/.test(text)
      && /y축에평행/.test(text)
      && /(?:점P.*점Q.*점R|P.*Q.*R)/.test(text),
    parabola_labeled_xintercepts: /(?:이차함수|포물선)/.test(text)
      && /x축/.test(text)
      && /(?:점A.*점B|A.*O.*B|A와B사이)/.test(text),
    parabola_xintercepts_vertex_triangle: /(?:이차함수|포물선)/.test(text)
      && /x축/.test(text)
      && /(?:점A.*점B|A.*B)/.test(text)
      && /(?:꼭짓점은?C|꼭짓점C|삼각형ABC)/.test(text),
    parabola_yaxis_xpositive_parallelogram: /(?:이차함수|포물선)/.test(text)
      && /y축/.test(text)
      && /평행사변형/.test(text)
      && /(?:점A|A의좌표)/.test(text),
    two_parabolas_axis_aligned_square: /(?:두|2)(?:개의?)?(?:이차함수|포물선)/.test(text)
      && /정사각형/.test(text)
      && /(?:x축|y축).*평행/.test(text),
    moving_points_right_triangle: /직각삼각형/.test(text)
      && /(?:점P.*점Q|P.*Q)/.test(text)
      && /(?:매초|속도|늘어|줄어|이동)/.test(text),
    moving_points_rectangle_triangle: /직사각형/.test(text)
      && /(?:점P.*점Q|P.*Q)/.test(text)
      && /(?:매초|속도|움직|이동)/.test(text),
    moving_point_rectangle_trapezoid: /직사각형/.test(text)
      && /(?:점P|P는|P가)/.test(text)
      && /(?:B에서C|BC를따라|선분BC)/.test(text)
      && /사다리꼴/.test(text),
    sliding_right_triangles_overlap: /(?:두|2|합동|같은)/.test(text)
      && /직각삼각형/.test(text)
      && /(?:이동|포개|겹치)/.test(text),
    activity_calorie_table: /표/.test(text)
      && /(?:줄넘기|배드민턴|활동)/.test(text)
      && /(?:10분|열량|kcal)/i.test(text),
    circle_with_two_semicircles: /(?:큰원|원O|원의내부|원내부)/.test(text)
      && /(?:두|2)(?:개의?)?반원/.test(text)
      && /지름/.test(text),
    annulus_area: !/반원/.test(text)
      && /(?:동심원|원띠|고리|중심이같은|중심이동일한)/.test(text),
    annulus_radius_increase: !/반원/.test(text)
      && /반지름/.test(text)
      && /(?:늘|증가)/.test(text)
  };
  if (checks[template] === false) {
    return '자동 선택된 ' + template + ' 템플릿이 원문 구조 검증을 통과하지 못했습니다.';
  }
  return '';
}

function buildImageTemplateEvidence_(template, value) {
  const text = String(value || '').replace(/\s+/g, '');
  const evidenceByTemplate = {
    rectangle_cross_road: ['직사각형', '도로/통로/길'],
    rectangle_slanted_cross_road: ['직사각형', '사선 도로/통로/길'],
    rectangle_parallel_roads: ['직사각형', '여러 도로/통로/길'],
    open_box_net_rectangular_paper: ['직사각형 종이', '네 귀퉁이', '정사각형 절단', '상자'],
    open_box_net_equal_cuts: ['종이', '네 귀퉁이', '정사각형 절단', '상자'],
    rectangle_square_similar_split: ['직사각형', '정사각형 절단', '남은 직사각형 닮음'],
    two_parabolas_shared_vertex_intersections: ['두 포물선', '서로의 꼭짓점 통과'],
    two_origin_parabolas_parallelogram: ['두 포물선', '원점', '평행사변형'],
    two_origin_parabolas_vertical_line_ratio: ['원점을 지나는 두 포물선', 'y축에 평행한 직선', 'P-Q-R'],
    parabola_labeled_xintercepts: ['단일 포물선', 'x축의 두 교점 A/B', '원점 O'],
    parabola_xintercepts_vertex_triangle: ['x축의 두 교점 A/B', '꼭짓점 C', '삼각형 ABC'],
    parabola_yaxis_xpositive_parallelogram: ['y축 위의 A', '포물선 위의 B/C/D', '평행사변형 ABCD'],
    two_parabolas_axis_aligned_square: ['두 포물선', '축에 평행한 정사각형', 'A/C가 서로 다른 포물선 위'],
    moving_points_right_triangle: ['직각삼각형 ABC', 'AB 위의 P', 'BC 방향의 Q', '두 변 길이 변화'],
    moving_points_rectangle_triangle: ['직사각형', '이동점 P/Q', '삼각형 넓이 변화'],
    moving_point_rectangle_trapezoid: ['직사각형 ABCD', 'BC 위 이동점 P', '사다리꼴 APCD'],
    sliding_right_triangles_overlap: ['두 합동 직각삼각형', '평행이동', '겹치는 부분'],
    activity_calorie_table: ['활동별 표', '10분당 소모 열량', 'kcal'],
    circle_with_two_semicircles: ['큰 원 내부', '두 반원', '지름 위 배치'],
    annulus_area: ['동심원/원띠/고리', '두 원의 공통 중심'],
    annulus_radius_increase: ['원의 반지름', '증가량']
  };
  const evidence = evidenceByTemplate[template] || [];
  return evidence.length
    ? template + ': ' + evidence.join(' + ')
    : template + ': 원문 문제본문과 이미지설명 규칙 일치';
}

function formatExistingTemplateHint_(hint) {
  if (!hint) return '자동 일치 없음 - 구현 템플릿 목록을 검토한 뒤 정확히 일치할 때만 사용';
  return 'template=' + hint.template + ' / 필수 항목=' + hint.requiredFields;
}

function getImplementedImageTemplateNames_() {
  return [
    'past_exam_image',
    'parabola_band_area', 'parabola_basic_shape', 'parabola_labeled_xintercepts',
    'parabola_xintercepts_vertex_triangle', 'parabola_xintercepts_yintercept_triangle',
    'parabola_yintercept_vertex_xintercept_triangle', 'two_origin_parabolas_horizontal_line',
    'two_origin_parabolas_vertical_line_ratio', 'two_parabolas_between_area',
    'parabola_family_origin', 'multiple_choice_parabola_position',
    'unit_quarter_circle_trig',
    'parabola_shift_from_base', 'two_parabolas_same_width_horizontal_chord',
    'rectangle_cross_road', 'rectangle_slanted_cross_road', 'rectangle_multi_slanted_roads',
    'rectangle_parallel_roads',
    'two_origin_parabolas_parallelogram', 'parabola_diamond_on_axes',
    'two_parabolas_square', 'two_parabolas_axis_aligned_square', 'two_parabolas_shared_vertex_intersections',
    'line_to_parabola_quadrant_match', 'annulus_area', 'annulus_radius_increase',
    'circle_with_two_semicircles', 'rectangle_point_triangle', 'square_expanded_garden',
    'rectangular_park_border', 'rectangle_diagonal_flower_path', 'two_squares_on_segment',
    'two_squares_from_segment', 'growing_rectangle', 'open_box_net_equal_cuts',
    'open_box_net_rectangular_paper', 'folded_tray', 'adjacent_rectangles',
    'moving_points_rectangle_triangle', 'moving_point_rectangle_trapezoid',
    'moving_points_right_triangle', 'right_isosceles_triangle_inner_rectangle',
    'sliding_right_triangles_overlap',
    'right_isosceles_triangle_parallelogram', 'tiled_rectangle_corner_square',
    'linear_basic_intercepts', 'linear_sign_diagram', 'linear_point_guides', 'linear_axis_triangle',
    'linear_two_lines_region', 'linear_two_lines_labeled_points', 'linear_parameter_triangle_cases',
    'linear_two_lines_xaxis_square',
    'linear_square_under_line', 'grid_number_table', 'activity_calorie_table',
    'tiled_rectangles_layout', 'regular_polygon_chain', 'regular_polygon_chain_sequence',
    'rectangle_side_point_triangle', 'rectangle_inner_slanted_quadrilateral',
    'rectangle_cut_corner', 'rectangle_expanding_sides', 'three_semicircles',
    'folded_rectangle_overlap', 'square_internal_rectangles', 'regular_polygon_diagonals',
    'linear_parallel_lines', 'multiple_choice_linear_position',
    'parabola_vertex_yintercept_origin_triangle',
    'parabola_xintercepts_vertex_yintercept_quadrilateral',
    'parabola_yaxis_xpositive_parallelogram', 'parabola_point_xaxis_triangle',
    'parabola_line_intersections_triangle', 'two_parabolas_lens_rectangle',
    'parabola_four_family_origin', 'parabola_axis_values', 'quadratic_motion_height',
    'parabolic_water_cross_section', 'parabola_horizontal_equal_intersections',
    'parabola_inscribed_square', 'coordinate_parallelogram',
    'two_parabolas_vertical_trapezoid',
    'two_parabolas_vertical_strip', 'parabola_horizontal_chord_rectangle',
    'parabola_vertex_horizontal_chord_triangle', 'three_parabolas_enclosed_region',
    'rectangle_corner_extension', 'stacked_blocks_pattern',
    'rectangle_u_shaped_path', 'linear_vertical_line_position', 'linear_vertical_line_triangle',
    'parallelogram_diagonal_intersection', 'collinear_two_squares',
    'square_cut_and_shift', 'rectangle_square_similar_split',
    'nested_rectangles_frame', 'triangular_dot_pattern', 'rectangular_dot_pattern',
    'right_triangle_equal_segments', 'square_rotated_inscribed',
    'isosceles_trapezoid_altitude', 'segment_square_triangle',
    'tiled_wall_gap', 'square_diagonal_paths', 'isosceles_triangle_bisector',
    'attached_rectangles_diagonal'
  ];
}

function formatImageTemplateForPrompt_(value) {
  const text = String(value || '').trim();
  if (!text) return '(없음)';
  return '\n' + text;
}

function isImageGenerationExample_(example) {
  const included = String(example['도형그래프포함여부'] || '').trim().toUpperCase();
  return included === 'TRUE'
    || included === 'Y'
    || included === 'YES'
    || included === '있음'
    || Boolean(String(example['도형그래프템플릿'] || '').trim())
    || Boolean(String(example['도형그래프설명'] || '').trim());
}

function validateGeneralProblemImageOutput_(text, exampleGroups) {
  let normalized = normalizeImagePromptBlocks_(
    String(text || '').replace(/\[그림\s*필요\s*:/g, '[이미지 필요:')
  );
  normalized = normalizePastExamParabolaChoicePrompts_(normalized);
  normalized = normalizeGeometryImagePrompts_(normalized);
  normalized = normalizeGeneratedNumberingStyle_(normalized);
  const numberingIssue = getGeneratedNumberingStyleIssue_(normalized);
  if (numberingIssue) {
    throw new Error('일반문항 표기 형식 오류: ' + numberingIssue);
  }
  if (hasDraftLeakText_({ body: normalized })) {
    throw new Error('일반문항 응답에 문제 수정 과정이나 초안 문장이 포함되었습니다.');
  }
  const expectedImageCount = exampleGroups.reduce((sum, group) => {
    const requiresImage = group.examples.some(isImageGenerationExample_);
    return sum + (requiresImage ? Number(group.item.count || 0) : 0);
  }, 0);
  if (!expectedImageCount) return normalized;

  const imageBlocks = normalized.match(/\[이미지\s*필요\s*:/g) || [];
  const structuredBlocks = normalized.match(/\[IMAGE_PROMPT\s*:/gi) || [];
  if (imageBlocks.length < expectedImageCount) {
    throw new Error(
      '이미지 문항 출력 누락: ' + expectedImageCount + '개가 필요하지만 [이미지 필요] 블록은 '
      + imageBlocks.length + '개입니다.'
    );
  }
  if (structuredBlocks.length < expectedImageCount) {
    throw new Error(
      '이미지 템플릿 출력 누락: ' + expectedImageCount + '개가 필요하지만 IMAGE_PROMPT 블록은 '
      + structuredBlocks.length + '개입니다.'
    );
  }

  const blocks = normalized.match(/\[IMAGE_PROMPT\s*:\s*[\s\S]*?\]/gi) || [];
  blocks.forEach((block, index) => {
    const commonError = getImagePromptBlockError_(block, index + 1);
    if (commonError) throw new Error(commonError);

    const templateMatch = block.match(/\btemplate\s*=\s*([a-z0-9_]+)\b/i);
    if (templateMatch) {
      const template = String(templateMatch[1] || '').toLowerCase();
      if (getImplementedImageTemplateNames_().indexOf(template) < 0) {
        throw new Error('지원되지 않는 IMAGE_PROMPT template입니다: ' + template);
      }
      if (template === 'rectangle_cross_road'
          && (!/\bwidth\s*=/i.test(block)
              || !/\bheight\s*=/i.test(block)
              || !/\broad_width\s*=/i.test(block))) {
        throw new Error(
          'rectangle_cross_road IMAGE_PROMPT ' + (index + 1)
          + '번에 width, height, road_width가 필요합니다.'
        );
      }
      if (template === 'linear_parameter_triangle_cases'
          && (!/\bequations\s*=/i.test(block)
              || !/\bparameter\s*=/i.test(block)
              || !/\bparameter_values\s*=/i.test(block))) {
        throw new Error(
          'linear_parameter_triangle_cases IMAGE_PROMPT ' + (index + 1)
          + '번에 equations, parameter, parameter_values가 필요합니다.'
        );
      }
      return;
    }
    if (!/\btype\s*=\s*(geometry|coordinate_plane)\b/i.test(block)) {
      throw new Error('IMAGE_PROMPT ' + (index + 1) + '번의 type이 없거나 지원되지 않습니다.');
    }
    if (/\btype\s*=\s*geometry\b/i.test(block)
        && (!/\bshape\s*=/i.test(block) || !/\b(coordinates|center)\s*=/i.test(block))) {
      throw new Error('geometry IMAGE_PROMPT ' + (index + 1) + '번에 shape와 coordinates/center가 필요합니다.');
    }
    if (/\btype\s*=\s*coordinate_plane\b/i.test(block)
        && !/\b(equation|points)\s*=/i.test(block)) {
      throw new Error('coordinate_plane IMAGE_PROMPT ' + (index + 1) + '번에 equation 또는 points가 필요합니다.');
    }
  });
  return normalized;
}

function formatGeneralProblemsResult_(payload, text) {
  const summary = getGeneralProblemRequestItems_(payload).map(item => {
    return item.bookName + ' / ' + item.unit1 + ' / ' + item.unit2 + ' / 유형' + item.typeNumber + ' / ' + item.count + '문제';
  }).join('\n');
  return [
    '대상: ' + (String(payload.studentName || '').trim() || String(payload.teacherId || '').trim() || '선생님'),
    '생성조건:',
    summary,
    '',
    String(text || '').trim()
  ].join('\n');
}

function handlePastExamProblems_(targetSheetName, targetRow, payload) {
  const teacherSheet = getTeacherSheetForTask_(targetSheetName, payload);
  if (!teacherSheet) throw new Error('선생님 시트를 찾을 수 없습니다: ' + targetSheetName);
  const teacherHeaders = getHeaderMap_(teacherSheet);
  let count = 0;

  const sources = lookupPastExamProblems_(payload);
  if (!sources.length) {
    throw new Error('기출문제은행에서 선택 조건과 일치하는 문제를 찾지 못했습니다.');
  }

  const progress = getPastExamProgressFile_(targetSheetName, payload);
  const completedNumbers = getCompletedProgressNumbers_(progress.file);
  let generatedCount = Object.keys(completedNumbers).length;
  const referenceSources = getUsablePastExamGenerationSources_(sources);
  count = referenceSources.length;
  payload.count = count;
  const chunkGroups = [];
  let searchNumber = 1;
  while (searchNumber <= count && chunkGroups.length < 6) {
    while (searchNumber <= count && completedNumbers[searchNumber]) searchNumber += 1;
    if (searchNumber > count) break;
    let chunkCount = 0;
    while (chunkCount < 5
        && searchNumber + chunkCount <= count
        && !completedNumbers[searchNumber + chunkCount]) {
      chunkCount += 1;
    }
    chunkGroups.push({
      count: chunkCount,
      startNumber: searchNumber,
      prompt: buildPastExamProblemsPrompt_(
        payload,
        referenceSources.slice(searchNumber - 1, searchNumber - 1 + chunkCount),
        searchNumber,
        chunkCount
      )
    });
    searchNumber += chunkCount;
  }
  const scope = getTeacherScopeForTask_(targetSheetName, payload);
  let responses;
  try {
    responses = callPaidGenerationBatch_(
      TASK_TYPES.PAST_EXAM_PROBLEMS,
      scope,
      chunkGroups.map(group => group.prompt)
    );
  } catch (error) {
    savePartialPastExamResponses_(
      progress.file,
      error.partialResults,
      chunkGroups,
      targetSheetName,
      payload
    );
    generatedCount = countProgressChunkItems_(progress.file);
    error.resumePayload = {
      pastExamProgressFileId: progress.file.getId(),
      pastExamGeneratedCount: generatedCount
    };
    setRowValues_(teacherSheet, targetRow, teacherHeaders, {
      '처리상태': 'RUNNING ' + generatedCount + '/' + count,
      '오류메시지': '성공한 ' + generatedCount + '문항은 보존했습니다. 실패 묶음만 재시도합니다.'
    });
    throw error;
  }
  const generatedParts = repairAndValidatePastExamProblemImagesBatch_(
    targetSheetName,
    payload,
    responses.map(response => response.text),
    chunkGroups.map(group => group.prompt)
  );
  generatedParts.forEach((generatedPart, index) => {
    const response = responses[index];
    const requestIndex = Number.isFinite(response.requestIndex) ? response.requestIndex : index;
    const group = chunkGroups[requestIndex];
    assertPastExamChunkProblemCount_(generatedPart, group.count);
    appendProgressTextChunk_(progress.file, generatedPart, group.count, group.startNumber);
  });
  generatedCount = countProgressChunkItems_(progress.file);

  if (generatedCount < count) {
    setRowValues_(teacherSheet, targetRow, teacherHeaders, {
      '처리상태': 'RUNNING ' + generatedCount + '/' + count,
      '오류메시지': ''
    });
    const nextPayload = Object.assign({}, payload, {
      pastExamProgressFileId: progress.file.getId(),
      pastExamGeneratedCount: generatedCount
    });
    return {
      continueLater: true,
      payload: nextPayload,
      message: '기출유사문항 분할 생성 진행 중: ' + generatedCount + '/' + count
    };
  }

  const finalText = formatPastExamProblemsResult_(payload, readProgressText_(progress.file));
  const fileUrl = finalizePastExamProgressFile_(targetSheetName, payload, progress.file, finalText);
  setRowValues_(teacherSheet, targetRow, teacherHeaders, {
    '생성결과': fileUrl,
    '처리상태': 'DONE',
    '오류메시지': ''
  });
  return { continueLater: false };
}

function savePartialPastExamResponses_(file, responses, chunkGroups, targetSheetName, payload) {
  (responses || []).forEach((response, index) => {
    const requestIndex = Number.isFinite(response.requestIndex) ? response.requestIndex : index;
    const group = chunkGroups[requestIndex];
    if (!group) return;
    try {
      const generatedParts = repairAndValidatePastExamProblemImagesBatch_(
        targetSheetName,
        payload,
        [response.text],
        [group.prompt]
      );
      assertPastExamChunkProblemCount_(generatedParts[0], group.count);
      appendProgressTextChunk_(
        file,
        generatedParts[0],
        group.count,
        group.startNumber
      );
    } catch (error) {
      // Invalid output is not checkpointed; only this group will be requested again.
    }
  });
}

function assertPastExamChunkProblemCount_(text, expectedCount) {
  const expected = Number(expectedCount || 0);
  const actual = countGeneratedProblemHeadings_(text);
  if (expected > 0 && actual !== expected) {
    throw new Error('기출유사문항 1:1 생성 개수 불일치: 요청 ' + expected + '문항, 응답 ' + actual + '문항');
  }
}

function countGeneratedProblemHeadings_(text) {
  const matches = String(text || '').match(/^\s*문항\s*\d+\s*[.)]/gm) || [];
  return matches.length;
}

function getPastExamProgressFile_(targetSheetName, payload) {
  const savedId = String(payload.pastExamProgressFileId || '').trim();
  if (savedId) {
    try {
      return {
        file: DriveApp.getFileById(savedId),
        generatedCount: countProgressChunkItems_(DriveApp.getFileById(savedId))
      };
    } catch (error) {
      // The temporary file may have been removed; restart this request from the first chunk.
    }
  }

  const rootFolderId = getDriveRootFolderId_();
  if (!rootFolderId) throw new Error('관리자_설정에 Drive루트폴더ID가 없습니다.');
  const ownerName = getGeneralProblemsOwnerName_(targetSheetName, payload);
  const root = DriveApp.getFolderById(rootFolderId);
  const folder = getOrCreateChildFolder_(root, sanitizeFileName_(ownerName));
  const requestKey = String(payload.requestedAt || Utilities.getUuid()).replace(/[^0-9A-Za-z]/g, '').slice(-18);
  const fileName = sanitizeFileName_(ownerName + '_기출유사문제_작업중_' + requestKey + '.txt');
  return {
    file: folder.createFile(fileName, '', MimeType.PLAIN_TEXT),
    generatedCount: 0
  };
}

function getGenerationProgressFile_(targetSheetName, payload, payloadKey, label) {
  const savedId = String(payload[payloadKey] || '').trim();
  if (savedId) {
    try {
      return { file: DriveApp.getFileById(savedId), isNew: false };
    } catch (error) {
      // The temporary file may have been removed; create a new progress file.
    }
  }

  const ownerName = getGeneralProblemsOwnerName_(targetSheetName, payload);
  let folder;
  if (payloadKey === 'twinProgressFileId' && String(payload.studentName || '').trim()) {
    folder = getStudentOutputFolder_(payload.studentName);
  } else {
    const rootFolderId = getDriveRootFolderId_();
    if (!rootFolderId) throw new Error('관리자_설정에 Drive루트폴더ID가 없습니다.');
    const root = DriveApp.getFolderById(rootFolderId);
    folder = getOrCreateChildFolder_(root, sanitizeFileName_(ownerName));
  }
  const requestKey = String(payload.requestedAt || Utilities.getUuid()).replace(/[^0-9A-Za-z]/g, '').slice(-18);
  const fileName = sanitizeFileName_(ownerName + '_' + label + '_작업중_' + requestKey + '.txt');
  return { file: folder.createFile(fileName, '', MimeType.PLAIN_TEXT), isNew: true };
}

function readJsonArrayFromFile_(file) {
  const text = file.getBlob().getDataAsString('UTF-8').trim();
  if (!text) return [];
  try {
    const parsed = JSON.parse(text);
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    throw new Error('분할 생성 임시파일을 읽지 못했습니다: ' + error.message);
  }
}

function appendProgressTextChunk_(file, generatedPart, itemCount, startNumber) {
  const previous = file.getBlob().getDataAsString('UTF-8').trim();
  const next = String(generatedPart || '').trim();
  const start = Math.max(0, Number(startNumber || 0));
  if (start && getCompletedProgressNumbersFromText_(previous)[start]) return;
  const marker = start
    ? '[[CODEX_CHUNK_START:' + start + ';COUNT:' + Number(itemCount || 0) + ']]'
    : '[[CODEX_CHUNK_COUNT:' + Number(itemCount || 0) + ']]';
  const marked = marker + '\n' + next;
  file.setContent(previous ? previous + '\n\n' + marked : marked);
}

function countProgressChunkItems_(file) {
  const text = file.getBlob().getDataAsString('UTF-8');
  const completed = getCompletedProgressNumbersFromText_(text);
  return Object.keys(completed).length;
}

function getCompletedProgressNumbers_(file) {
  return getCompletedProgressNumbersFromText_(file.getBlob().getDataAsString('UTF-8'));
}

function getCompletedProgressNumbersFromText_(text) {
  const completed = {};
  const source = String(text || '');
  const legacyPattern = /\[\[CODEX_CHUNK_COUNT:(\d+)\]\]/g;
  let legacyTotal = 0;
  let legacyMatch;
  while ((legacyMatch = legacyPattern.exec(source)) !== null) {
    legacyTotal += Number(legacyMatch[1] || 0);
  }
  for (let number = 1; number <= legacyTotal; number += 1) {
    completed[number] = true;
  }

  const pattern = /\[\[CODEX_CHUNK_START:(\d+);COUNT:(\d+)\]\]/g;
  let match;
  while ((match = pattern.exec(source)) !== null) {
    const start = Number(match[1] || 0);
    const count = Number(match[2] || 0);
    for (let offset = 0; offset < count; offset += 1) {
      completed[start + offset] = true;
    }
  }
  return completed;
}

function readProgressText_(file) {
  const text = file.getBlob().getDataAsString('UTF-8');
  const chunks = [];
  const pattern = /\[\[CODEX_CHUNK_START:(\d+);COUNT:(\d+)\]\]\s*([\s\S]*?)(?=\n{2,}\[\[CODEX_CHUNK_(?:START|COUNT):|$)/g;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    chunks.push({ start: Number(match[1] || 0), text: String(match[3] || '').trim() });
  }
  const numberedText = chunks
    .sort((a, b) => a.start - b.start)
    .map(chunk => chunk.text)
    .join('\n\n');
  const legacyText = text
    .replace(/\[\[CODEX_CHUNK_START:\d+;COUNT:\d+\]\]\s*[\s\S]*?(?=\n{2,}\[\[CODEX_CHUNK_(?:START|COUNT):|$)/g, '')
    .replace(/\[\[CODEX_CHUNK_COUNT:\d+\]\]\s*/g, '')
    .trim();
  return [legacyText, numberedText].filter(Boolean).join('\n\n').trim();
}

function finalizePastExamProgressFile_(targetSheetName, payload, file, finalText) {
  const ownerName = getGeneralProblemsOwnerName_(targetSheetName, payload);
  const dateText = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyyMMdd');
  renameFileUniquely_(file, ownerName + '_' + dateText + '_기출유사문제.txt');
  file.setContent(finalText);
  return file.getUrl();
}

function lookupPastExamProblems_(payload) {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, SHEETS.PAST_EXAM_BANK, HEADERS.PAST_EXAM_BANK);
  const schoolName = String(payload.schoolName || '').trim();
  const grade = normalizeComparableText_(payload.grade);
  const semester = normalizeComparableText_(payload.semester);
  const examType = normalizeComparableText_(payload.examType);
  const years = parsePastExamYears_(payload.yearsText);

  return readObjects_(sheet)
    .map(item => item.rowObject)
    .filter(row => String(row['사용여부'] || 'TRUE').toUpperCase() !== 'FALSE')
    .filter(row => String(row['학교명'] || '').trim() === schoolName)
    .filter(row => normalizeComparableText_(row['학년']) === grade)
    .filter(row => normalizeComparableText_(row['학기']) === semester)
    .filter(row => normalizeComparableText_(row['시험구분']) === examType)
    .filter(row => !years.length || years.indexOf(normalizeYear_(row['연도'])) !== -1);
}

function parsePastExamYears_(value) {
  return unique_(String(value || '')
    .split(/[,/|\s]+/)
    .map(normalizeYear_)
    .filter(Boolean));
}

function normalizeYear_(value) {
  const match = String(value || '').match(/\d{2,4}/);
  if (!match) return '';
  const number = Number(match[0]);
  return number < 100 ? String(2000 + number) : String(number);
}

function normalizeComparableText_(value) {
  return String(value || '').replace(/\s+/g, '').trim();
}

function samplePastExamSources_(sources, maxItems) {
  if (sources.length <= maxItems) return sources;
  const byYear = {};
  sources.forEach(source => {
    const year = normalizeYear_(source['연도']) || '기타';
    if (!byYear[year]) byYear[year] = [];
    byYear[year].push(source);
  });

  const years = Object.keys(byYear).sort();
  const sampled = [];
  let index = 0;
  while (sampled.length < maxItems) {
    let added = false;
    years.forEach(year => {
      if (sampled.length >= maxItems) return;
      if (byYear[year][index]) {
        sampled.push(byYear[year][index]);
        added = true;
      }
    });
    if (!added) break;
    index += 1;
  }
  return sampled;
}

function getUsablePastExamGenerationSources_(sources) {
  return sources || [];
}

function isPastExamImageIncluded_(source) {
  const included = String(source && source['도형그래프포함여부'] || '').trim().toUpperCase();
  return included === 'TRUE'
    || included === 'Y'
    || included === 'YES'
    || included === '있음'
    || Boolean(String(source && source['이미지설명'] || '').trim())
    || Boolean(String(source && source['이미지링크'] || '').trim());
}

function buildPastExamProblemsPrompt_(payload, sources, startNumber, count) {
  const sourceText = sources.map((source, index) => {
    const pastExamImageId = String(source['기출이미지ID'] || '').trim();
    const explicitTemplate = String(source['이미지템플릿'] || '').trim();
    const explicitRequiredFields = String(source['이미지필수항목'] || '').trim();
    const templateHint = explicitTemplate
      ? { template: explicitTemplate, requiredFields: explicitRequiredFields }
      : findExistingImageTemplate_([
          source['상위단원'],
          source['하위단원'],
          source['문제유형'],
          source['문제본문'],
          source['이미지설명']
        ].join(' '));
    return [
    '[기출자료 ' + (index + 1) + ']',
    '학교/연도: ' + source['학교명'] + ' / ' + source['연도'],
    '학년/학기/시험: ' + source['학년'] + ' / ' + source['학기'] + ' / ' + source['시험구분'],
    '문제번호: ' + source['문제번호'],
    '단원: ' + String(source['상위단원'] || '') + ' > ' + String(source['하위단원'] || ''),
    '문제유형: ' + String(source['문제유형'] || ''),
    '난이도: ' + String(source['난이도'] || ''),
    '문제본문: ' + String(source['문제본문'] || ''),
    '정답: ' + String(source['정답'] || ''),
    '해설: ' + String(source['해설'] || ''),
    '도형/그래프 포함: ' + String(source['도형그래프포함여부'] || ''),
    '이미지설명: ' + String(source['이미지설명'] || ''),
    '이미지템플릿: ' + (explicitTemplate || (templateHint && templateHint.template) || ''),
    '이미지필수항목: ' + explicitRequiredFields,
    '기출이미지ID: ' + pastExamImageId,
    '이미지템플릿등록상태: ' + (explicitTemplate || (templateHint && templateHint.template) || !isPastExamImageIncluded_(source) ? 'OK' : 'REDRAW_REQUIRED'),
    '기존 템플릿 검색결과: ' + formatExistingTemplateHint_(templateHint)
    ].join('\n');
  }).join('\n\n');

  return [
    '너는 중학교 수학 시험 문항을 제작하는 교사다.',
    '아래에 제공된 지정 학교의 선택 연도 기출자료만 참고하여 유사문항을 생성하라.',
    '- 참고자료 1개당 유사문항을 정확히 1개만 생성하라.',
    '- 이번 묶음에 제공된 참고자료 수와 출력 문항 수는 반드시 같아야 한다.',
    '- 참고자료 순서를 유지하여 첫 번째 참고자료는 이번 묶음 시작번호, 두 번째 참고자료는 다음 번호로 출력하라.',
    '- 어떤 참고자료도 건너뛰거나, 하나의 참고자료에서 2문항 이상 생성하지 마라.',
    '절대 규칙:',
    getStandardProblemNumberingPromptRules_().join('\n'),
    '- 아래 기출자료에 실제로 나타난 단원과 문제유형만 사용하라.',
    '- 다른 학교, 다른 교재, 일반적인 외부 유형을 추가하지 마라.',
    '- 각 생성 문항은 반드시 참고한 기출자료 하나를 명확히 정하고, 출처유형에 그 연도/원본문제번호/문제유형을 정확히 적어라.',
    '- 문제의 문항 구조, 그림 구조, 풀이 원리는 선택한 원본 기출자료와 동일 계열이어야 한다. 서로 다른 기출자료의 문제와 그림을 섞지 마라.',
    '- 기출 원문을 그대로 복제하지 말고 숫자, 조건, 문장, 상황을 바꿔라.',
    '- 원본 기출문항에 나온 핵심 수치, 계수, 좌표, 길이, 넓이, 각도, 정답 값을 그대로 재사용하지 마라.',
    '- 원본과 같은 정답이 반복되지 않도록 수치와 조건을 재설계하라.',
    '- 새 수치는 계산이 깔끔하게 떨어지도록 설계하라. 정답과 중간값은 정수 또는 기약분수 꼴이 되게 하라.',
    '- 3.087918 같은 긴 소수, 근삿값, 무리하게 반올림한 값은 절대 사용하지 마라.',
    '- 분수 값은 가능하면 7/3, -5/2처럼 기약분수로 표기하고, 2.333333 같은 소수 표기는 쓰지 마라.',
    '- 기출자료의 유형 비중과 난이도 분포를 가능한 한 비슷하게 유지하라.',
    '- 도형이나 그래프가 필요한 유형은 생성 문제에도 반드시 포함하라.',
    '- 도형이나 그래프는 반드시 [이미지 필요: ...] 형식으로 구체적으로 설명하라.',
    '- [그림 필요: ...] 표현은 절대 사용하지 마라.',
    '- 이미지템플릿등록상태가 REDRAW_REQUIRED이어도 원본 기출자료로 선택할 수 있다. 이 경우 문제본문과 이미지설명을 바탕으로 구현 템플릿 또는 type=coordinate_plane/type=geometry로 새로 그려라.',
    '- 선택한 원본 기출자료의 기존 템플릿 검색결과가 있으면 반드시 그 template과 필수 항목을 사용하라.',
    '- 기출유사문항에서는 template=past_exam_image를 사용하지 마라. 원본 이미지를 재사용하지 말고 구현된 도표렌더러 템플릿이나 type=coordinate_plane/type=geometry로 새로 그려라.',
    '- 템플릿 검색결과가 있는데 다른 template이나 범용 type으로 바꾸지 마라.',
    '- 자동 검색결과가 없을 때만 구현 템플릿 목록을 검토하고, 원본 그림 구조와 정확히 일치할 때만 사용하라.',
    '- 구현 템플릿 목록: ' + getImplementedImageTemplateNames_().join(', '),
    '- 이미지가 필요한 문항은 [이미지 필요: ...] 바로 다음에 [IMAGE_PROMPT: ...] 블록을 출력하라.',
    '- 기존 템플릿을 쓸 때는 IMAGE_PROMPT에 template=템플릿명과 필요한 key=value를 넣어라.',
    '- rectangle_square_similar_split은 width, height, square_side에 렌더링 비율용 실제 숫자를 넣고, 문제에 문자 길이가 제시되면 width_label, height_label에 인쇄할 표기(예: 6, x)를 반드시 따로 넣어라. 근삿값 소수를 길이 라벨로 인쇄하지 마라.',
    '- 일치하는 기존 템플릿이 없을 때만 type=geometry 또는 type=coordinate_plane 범용 형식을 사용하라.',
    '- type=coordinate_plane에서는 equation 또는 points를 반드시 넣어라. 여러 식은 equation=y=...; y=...처럼 한 줄에 세미콜론으로 구분하라. x_range와 y_range는 렌더러가 자동 계산하므로 필요한 경우에만 넣어라.',
    '- equation에는 a, b, p, q 같은 값이 정해지지 않은 문자를 절대 남기지 마라. 문제에서 결정된 실제 숫자를 대입한 식만 적어라.',
    '- points에 적은 모든 점은 문제 조건과 equation을 직접 대입하여 일치하는지 검산하라.',
    '- 점이 3개 이상이면 segments=, polygon=, rectangle_points= 중 하나로 어떤 점을 연결할지 반드시 적어라. 점만 나열하지 마라.',
    '- 사각형이면 네 꼭짓점의 순서를 명시하고 segments=AB,BC,CD,DA 또는 polygon=A,B,C,D를 적어라.',
    '- multiple_choice_parabola_position은 choices=y=식1; y=식2; y=식3; y=식4; y=식5 형식만 사용하라.',
    '- choices에 JSON 배열, 대괄호, 따옴표, 설명 문장, 그래프 성질 보기를 넣지 마라. 반드시 실제 이차함수 식 5개만 넣어라.',
    '- line_to_parabola_quadrant_match는 line_equation=y=숫자식, parabola_form=y=숫자식만 사용하라. a, b, k, p, q 같은 미정계수를 절대 남기지 마라.',
    '- 세 반원 그림은 반드시 template=three_semicircles, diameter=전체 지름의 실제 숫자, split=점 C까지의 실제 숫자를 사용하라. diameter_AB, diameter_large, radius_AC 같은 임의 키를 만들지 마라.',
    '- 사분원 삼각비 그림은 반드시 template=unit_quarter_circle_trig, angle=각도의 실제 숫자를 사용하라.',
    '- 단일 포물선은 multiple_choice_parabola_position 또는 parabola_family_origin을 사용하지 마라. template=parabola_basic_shape과 equation=숫자가 확정된 식을 사용하라.',
    '- 기출유사문항에서는 template=parabola_family_origin과 template=past_exam_image를 사용하지 마라. 여러 포물선 계수 비교 그림은 반드시 확정 숫자식이 들어가는 다른 템플릿이나 type=coordinate_plane을 사용하라.',
    '- 포물선이 x축과 A, B에서 만나고 원점 O가 A와 B 사이에 표시되는 유형은 template=parabola_labeled_xintercepts를 사용하라. equation에는 조건으로 확정한 숫자 식을, curve_label에는 문제에 인쇄할 원래 식을 적어라.',
    '- 포물선의 x절편이 A, B이고 꼭짓점이 C인 삼각형은 template=parabola_xintercepts_vertex_triangle, equation=숫자가 확정된 식을 사용하라.',
    '- y축 위의 A와 포물선 위의 B, C, D가 평행사변형을 이루고 AD가 x축과 평행한 유형은 template=parabola_yaxis_xpositive_parallelogram, equation=숫자가 확정된 식, y_axis_y=A의 y좌표를 사용하라.',
    '- 직각삼각형 ABC에서 P가 AB를 따라 움직이고 Q가 BC 방향으로 움직이는 유형은 template=moving_points_right_triangle, vertical_leg, horizontal_leg, point_p_speed, point_q_speed를 사용하라.',
    '- 서로 다른 두 포물선 사이에 각 변이 좌표축과 평행한 정사각형은 template=two_parabolas_axis_aligned_square, equation_left, equation_right, square_side를 사용하라.',
    '- 원점을 지나는 두 포물선을 하나의 y축 평행선이 P, Q에서 만나고 x축의 점이 R인 길이비 유형은 template=two_origin_parabolas_vertical_line_ratio, equation1, equation2, vertical_x를 사용하라.',
    '- x_range와 y_range를 쓸 때는 x_range=-5,5처럼 쉼표로 구분한 숫자 2개만 사용하라. -1_5 같은 표기는 금지한다.',
    '- 문제 본문의 수치, 정답, 해설, [이미지 필요], IMAGE_PROMPT의 식과 좌표가 서로 완전히 같아야 한다.',
    '- IMAGE_PROMPT를 출력하기 전에 다음을 내부 검산하라: 식의 모든 계수 확정, 모든 좌표 대입 일치, 도형 연결 정보 존재, 객관식 식 개수 정확히 5개.',
    '- 모든 문제의 정답을 직접 검산하고 해설과 일치시켜라.',
    '- 문제에 별도의 <보기>가 필요한 경우 반드시 <보기>와 </보기> 태그로 감싸라. HWP 생성기는 이 블록을 1x1 표로 변환한다.',
    '- <보기> 안에는 보기 내용만 넣고 ①~⑤ 선택지는 <보기> 밖에 작성하라.',
    '- 해설은 핵심 식과 결론만 최대 6줄로 작성하라. 문제 재진술, 단계 번호, 반복 계산, 검산 과정은 쓰지 마라.',
    '- 5지선다형과 단답형은 가능하면 2~4줄로 끝내라.',
    '- 문제나 숫자를 수정한 과정, 실패한 계산, 대안 문제, 사과문은 절대 출력하지 마라.',
    '- LaTeX, 마크다운 표, 코드블록은 사용하지 말고 일반 텍스트로 작성하라.',
    '',
    '출력 형식:',
    '문항N.',
    '출처유형: 연도 / 원본문제번호 / 문제유형',
    '문제: ...',
    '[이미지 필요: 새 수치가 반영된 도형 또는 그래프 설명]',
    '[IMAGE_PROMPT:',
    'template=기존_템플릿명',
    '필수항목=새_문제의_값',
    ']',
    '정답: ...',
    '해설: ...',
    '',
    '반드시 이번 요청 데이터에 지정된 문항 수만 출력하라.',
    '',
    '이번 요청 데이터:',
    '학교명: ' + payload.schoolName,
    '학년: ' + payload.grade,
    '대상연도: ' + payload.yearsText,
    '학기: ' + payload.semester,
    '시험구분: ' + payload.examType,
    '이번 묶음 문항번호: ' + startNumber + '번부터 ' + (startNumber + count - 1) + '번',
    '이번 묶음 생성 수: ' + count,
    '이번 묶음 참고자료 수: ' + sources.length,
    '생성 원칙: 참고자료 1개당 유사문항 1개, 총 ' + sources.length + '문항',
    '',
    '참고할 수 있는 기출자료:',
    sourceText
  ].join('\n');
}

function validatePastExamProblemImageOutput_(text) {
  let normalized = normalizeImagePromptBlocks_(
    String(text || '').replace(/\[그림\s*필요\s*:/g, '[이미지 필요:')
  );
  normalized = normalizePastExamParabolaChoicePrompts_(normalized);
  normalized = normalizeGeometryImagePrompts_(normalized);
  normalized = normalizeGeneratedNumberingStyle_(normalized);
  const numberingIssue = getGeneratedNumberingStyleIssue_(normalized);
  if (numberingIssue) {
    throw new Error('기출 유사문항 표기 형식 오류: ' + numberingIssue);
  }
  if (hasDraftLeakText_({ body: normalized })) {
    throw new Error('기출 유사문항 응답에 문제 수정 과정이나 초안 문장이 포함되었습니다.');
  }
  const imageCount = (normalized.match(/\[이미지\s*필요\s*:/g) || []).length;
  const blocks = normalized.match(/\[IMAGE_PROMPT\s*:\s*[\s\S]*?\]/gi) || [];
  if (blocks.some(block => /\btemplate\s*=\s*past_exam_image\b/i.test(block))) {
    throw new Error('기출 유사문항 이미지 템플릿 오류: template=past_exam_image는 사용하지 않습니다. 구현 템플릿 또는 type=coordinate_plane/type=geometry로 다시 그리세요.');
  }
  if (blocks.length < imageCount) {
    throw new Error(
      '기출 유사문항 이미지 템플릿 누락: 이미지 문항 ' + imageCount
      + '개 중 IMAGE_PROMPT는 ' + blocks.length + '개입니다.'
    );
  }
  blocks.forEach((block, index) => {
    const commonError = getImagePromptBlockError_(block, index + 1);
    if (commonError) throw new Error(commonError);
  });
  const indexedBlocks = [];
  const indexedBlockPattern = /\[IMAGE_PROMPT\s*:\s*[\s\S]*?\]/gi;
  let indexedBlockMatch;
  while ((indexedBlockMatch = indexedBlockPattern.exec(normalized)) !== null) {
    indexedBlocks.push({
      block: indexedBlockMatch[0],
      index: indexedBlockMatch.index
    });
  }
  indexedBlocks.forEach((item, index) => {
    const prefix = normalized.slice(0, item.index);
    const descriptionPattern = /\[이미지\s*필요\s*:\s*([\s\S]*?)\]/gi;
    let descriptionMatch;
    let nearestDescription = '';
    while ((descriptionMatch = descriptionPattern.exec(prefix)) !== null) {
      nearestDescription = String(descriptionMatch[1] || '').trim();
    }
    const matchError = getPastExamImageDescriptionMatchError_(
      nearestDescription,
      item.block,
      index + 1
    );
    if (matchError) throw new Error(matchError);
  });
  return normalized.trim();
}

function getPastExamImageDescriptionMatchError_(description, block, index) {
  const compact = String(description || '').replace(/\s+/g, '');
  const templateMatch = String(block || '').match(/\btemplate\s*=\s*([a-z0-9_]+)\b/i);
  const template = templateMatch ? String(templateMatch[1] || '').toLowerCase() : '';

  if (/(?:세|3개의?)반원/.test(compact) && template !== 'three_semicircles') {
    return '기출 IMAGE_PROMPT ' + index
      + '번은 세 반원 그림이므로 template=three_semicircles를 사용해야 합니다.';
  }
  if (/(?:사분원).*(?:삼각비|sin|cos|tan|직각삼각형)/i.test(compact)
      && template !== 'unit_quarter_circle_trig') {
    return '기출 IMAGE_PROMPT ' + index
      + '번은 사분원 삼각비 그림이므로 template=unit_quarter_circle_trig를 사용해야 합니다.';
  }
  const multipleParabolas = /(?:5개|다섯개|보기).*(?:이차함수|포물선|그래프)/.test(compact);
  if (multipleParabolas && template !== 'multiple_choice_parabola_position') {
    return '기출 IMAGE_PROMPT ' + index
      + '번은 포물선 객관식 보기이므로 template=multiple_choice_parabola_position을 사용해야 합니다.';
  }
  const singleParabola = /(?:이차함수|포물선).*(?:꼭짓점|y절편|x절편)/.test(compact)
    && !multipleParabolas;
  if (singleParabola
      && (template === 'multiple_choice_parabola_position'
          || template === 'parabola_family_origin')) {
    return '기출 IMAGE_PROMPT ' + index
      + '번은 단일 포물선인데 여러 그래프용 template=' + template + '를 사용했습니다.';
  }
  return '';
}

function repairAndValidatePastExamProblemImages_(targetSheetName, payload, text) {
  let currentText = String(text || '');
  let lastError = null;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      return validatePastExamProblemImageOutput_(currentText);
    } catch (error) {
      lastError = error;
      if (attempt >= 1) break;
      const repairPrompt = buildPastExamImageRepairPrompt_(currentText, error.message);
      const response = callGemini_(
        TASK_TYPES.PAST_EXAM_PROBLEMS,
        getTeacherScopeForTask_(targetSheetName, payload),
        repairPrompt,
        []
      );
      currentText = response.text;
    }
  }
  throw lastError;
}

function repairAndValidatePastExamProblemImagesBatch_(targetSheetName, payload, texts, originalPrompts) {
  const results = new Array(texts.length);
  const repairIndexes = [];
  const repairPrompts = [];

  texts.forEach((text, index) => {
    try {
      results[index] = validatePastExamProblemImageOutput_(text);
    } catch (error) {
      repairIndexes.push(index);
      repairPrompts.push(buildPastExamImageRepairPrompt_(text, error.message));
    }
  });
  if (!repairPrompts.length) return results;

  const repairs = callPaidGenerationBatch_(
    TASK_TYPES.PAST_EXAM_PROBLEMS,
    getTeacherScopeForTask_(targetSheetName, payload),
    repairPrompts
  );
  const regenerateIndexes = [];
  const regeneratePrompts = [];
  repairs.forEach((response, repairIndex) => {
    const resultIndex = repairIndexes[repairIndex];
    try {
      results[resultIndex] = validatePastExamProblemImageOutput_(response.text);
    } catch (error) {
      regenerateIndexes.push(resultIndex);
      regeneratePrompts.push(buildPastExamFullRegenerationPrompt_(
        originalPrompts && originalPrompts[resultIndex],
        response.text,
        error.message
      ));
    }
  });
  if (!regeneratePrompts.length) return results;

  const regenerated = callPaidGenerationBatch_(
    TASK_TYPES.PAST_EXAM_PROBLEMS,
    getTeacherScopeForTask_(targetSheetName, payload),
    regeneratePrompts
  );
  const finalRepairIndexes = [];
  const finalRepairPrompts = [];
  regenerated.forEach((response, regenerateIndex) => {
    const resultIndex = regenerateIndexes[regenerateIndex];
    try {
      results[resultIndex] = validatePastExamProblemImageOutput_(response.text);
    } catch (error) {
      finalRepairIndexes.push(resultIndex);
      finalRepairPrompts.push(buildPastExamImageRepairPrompt_(response.text, error.message));
    }
  });
  if (!finalRepairPrompts.length) return results;

  const finalRepairs = callPaidGenerationBatch_(
    TASK_TYPES.PAST_EXAM_PROBLEMS,
    getTeacherScopeForTask_(targetSheetName, payload),
    finalRepairPrompts
  );
  finalRepairs.forEach((response, finalRepairIndex) => {
    const resultIndex = finalRepairIndexes[finalRepairIndex];
    results[resultIndex] = validatePastExamProblemImageOutput_(response.text);
  });
  return results;
}

function buildPastExamFullRegenerationPrompt_(originalPrompt, failedText, errorMessage) {
  return [
    '너는 기출 유사문항 생성 결과를 전체 재생성하는 수학 교사다.',
    '이전 생성 결과가 이미지 검증 오류로 폐기되었다.',
    '이전 결과를 부분 수정하거나 줄이지 말고, 위 요청의 해당 묶음 문항 전체를 처음부터 다시 생성하라.',
    '모든 [이미지 필요: ...]마다 바로 다음에 정확히 하나의 완전한 [IMAGE_PROMPT: ...]를 작성하라.',
    '문항 수와 문항번호 범위를 반드시 원래 요청과 동일하게 유지하라.',
    'IMAGE_PROMPT 개수는 [이미지 필요:] 개수와 정확히 같아야 한다.',
    '원본 기출문항의 핵심 수치를 그대로 재사용하지 말고, 새 수치는 정수 또는 기약분수 꼴로 계산이 떨어지게 설계하라.',
    '3.087918 같은 긴 소수와 근삿값은 절대 쓰지 마라.',
    '',
    '원래 생성 요청:',
    String(originalPrompt || ''),
    '',
    '검증 오류:',
    String(errorMessage || ''),
    '',
    '폐기된 이전 결과는 오류 형태를 참고하는 용도로만 사용하라:',
    String(failedText || '')
  ].join('\n');
}

function buildPastExamImageRepairPromptLegacy_(generatedText, errorMessage) {
  return [
    '아래는 이미 생성된 기출 유사문항 결과다.',
    '문제, 문항번호, 출처유형, 정답, 해설, 수치와 문장을 변경하지 마라.',
    '검증 오류를 해결하기 위해 이미지 관련 블록만 보정한 전체 결과를 다시 출력하라.',
    '',
    '검증 오류: ' + errorMessage,
    '',
    '보정 규칙:',
    '- 모든 [이미지 필요: ...] 바로 다음에는 정확히 하나의 [IMAGE_PROMPT: ...]가 있어야 한다.',
    '- 기존 IMAGE_PROMPT가 올바르면 그대로 유지하라.',
    '- 누락된 IMAGE_PROMPT만 이미지 설명과 문제 내용을 바탕으로 추가하라.',
    '- 구현 템플릿 목록: ' + getImplementedImageTemplateNames_().join(', '),
    '- 맞는 구현 템플릿이 있으면 template=이름과 필수 key=value를 사용하라.',
    '- 새로 넣는 변수값은 정수 또는 기약분수 꼴이어야 하며 긴 소수와 근삿값은 금지한다.',
    '- 맞는 템플릿이 없으면 type=geometry 또는 type=coordinate_plane을 사용하라.',
    '- type=coordinate_plane은 equation 또는 points를 반드시 포함하라. 여러 식은 equation 값에서 세미콜론으로 구분하라.',
    '- type=geometry는 shape와 coordinates 또는 center를 반드시 포함하라.',
    '- 설명 문장, 사과, 코드블록을 추가하지 말고 보정된 전체 문항 결과만 반환하라.',
    '',
    '보정할 전체 결과:',
    generatedText
  ].join('\n');
}

function buildPastExamImageRepairPrompt_(generatedText, errorMessage) {
  return [
    '아래는 이미 생성된 기출 유사문항 결과다.',
    '문제, 문항번호, 출처유형, 정답, 해설, 수치와 문장은 변경하지 마라.',
    '검증 오류를 해결하기 위해 이미지 관련 블록만 보정한 전체 결과를 다시 출력하라.',
    '',
    '보정 규칙:',
    '- 모든 [이미지 필요: ...] 바로 다음에는 정확히 하나의 [IMAGE_PROMPT: ...]가 있어야 한다.',
    '- 기존 IMAGE_PROMPT가 올바르면 그대로 유지하고, 오류가 있는 블록만 이미지 설명과 문제 내용에 맞게 고쳐라.',
    '- 선택된 원본 기출문항과 기존 템플릿 검색결과를 그대로 기준으로 삼아라. 다른 기출문항의 구조나 다른 템플릿을 섞지 마라.',
    '- 구현 템플릿 목록: ' + getImplementedImageTemplateNames_().join(', '),
    '- 기존 템플릿 검색결과에 template=...이 있으면 해당 템플릿과 필수 key=value를 반드시 사용하라.',
    '- 기존 템플릿 검색결과가 template=past_exam_image여도 이를 유지하지 마라. 구현 템플릿 또는 type=coordinate_plane/type=geometry로 새로 그려라.',
    '- template=past_exam_image는 사용하지 마라. 원본 이미지 재사용 방식은 폐기되었다.',
    '- IMAGE_PROMPT에 3.087918 같은 긴 소수와 근삿값을 넣지 마라. 분수는 7/3처럼 적어라.',
    '- 템플릿 검색결과가 있는데 편의상 type=coordinate_plane 또는 type=geometry로 바꾸지 마라.',
    '- 적합한 구현 템플릿이 없을 때만 type=geometry 또는 type=coordinate_plane을 사용하라.',
    '- type=coordinate_plane의 equation에는 a, b, p, q 같은 미정계수를 남기지 말고 실제 숫자로 완성된 식만 넣어라.',
    '- coordinate_plane의 모든 점은 equation과 문제 조건에 직접 대입하여 일치하는지 검산하라.',
    '- 점이 3개 이상이면 segments=, polygon=, rectangle_points= 중 하나로 연결 관계를 반드시 명시하라. 점만 나열하지 마라.',
    '- 사각형은 네 꼭짓점을 둘레 순서로 적고 segments=AB,BC,CD,DA 또는 polygon=A,B,C,D를 반드시 넣어라.',
    '- multiple_choice_parabola_position은 choices=y=식1; y=식2; y=식3; y=식4; y=식5 형식만 사용하라.',
    '- choices에 JSON 배열, 대괄호, 따옴표, 설명 문장, 그래프 성질 보기를 넣지 마라. 실제 이차함수 식은 정확히 5개여야 한다.',
    '- line_to_parabola_quadrant_match는 line_equation=y=숫자식, parabola_form=y=숫자식만 사용하라. a, b, k, p, q 같은 미정계수를 절대 남기지 마라.',
    '- 세 반원 그림은 template=three_semicircles, diameter=실제 숫자, split=실제 숫자만 사용하라. 다른 키 이름을 만들지 마라.',
    '- 사분원 삼각비 그림은 template=unit_quarter_circle_trig, angle=실제 숫자를 사용하라.',
    '- 단일 포물선은 template=parabola_basic_shape, equation=숫자가 확정된 식을 사용하라.',
    '- 기출유사문항 보정에서 template=parabola_family_origin과 template=past_exam_image를 사용하지 마라. 미정계수가 남기 쉬우므로 확정 숫자식 템플릿이나 type=coordinate_plane으로 바꿔라.',
    '- 포물선의 x절편이 A, B이고 원점 O가 그 사이인 그림은 template=parabola_labeled_xintercepts, equation=숫자로 확정된 식, curve_label=문제에 표시할 식을 사용하라.',
    '- 포물선의 x절편 A, B와 꼭짓점 C를 이은 삼각형은 template=parabola_xintercepts_vertex_triangle과 equation을 사용하라.',
    '- y축 위의 A와 포물선 위의 B, C, D가 평행사변형을 이루는 그림은 template=parabola_yaxis_xpositive_parallelogram, equation, y_axis_y=A의 y좌표를 사용하라.',
    '- 직각삼각형의 두 직각변 위 또는 연장선에서 P, Q가 움직이는 그림은 template=moving_points_right_triangle과 네 필수 수치를 사용하라.',
    '- 두 포물선 사이의 축평행 정사각형은 template=two_parabolas_axis_aligned_square, equation_left, equation_right, square_side를 사용하라.',
    '- 두 원점 포물선과 수직선 위 P-Q-R 길이비 그림은 template=two_origin_parabolas_vertical_line_ratio, equation1, equation2, vertical_x를 사용하라.',
    '- 활동별 10분당 소모 열량 표는 template=activity_calorie_table, activities=활동명 목록, calories_per_10min=열량 목록을 사용하라.',
    '- type=geometry에는 shape와 coordinates 또는 center를 반드시 넣어라.',
    '- 문제에 없는 좌표값, 길이, 각도, 정답 힌트를 그림에 임의로 표시하지 마라.',
    '- 문제 본문, 정답, 해설, 이미지 설명, IMAGE_PROMPT의 식과 좌표가 모두 일치하는지 마지막에 검산하라.',
    '- 설명 문장, 사과, 코드블록은 추가하지 말고 보정된 전체 문항 결과만 반환하라.',
    '',
    '검증 오류:',
    String(errorMessage || ''),
    '',
    '보정할 전체 결과:',
    generatedText
  ].join('\n');
}

function formatPastExamProblemsResult_(payload, text) {
  return [
    '대상: ' + (String(payload.studentName || '').trim() || String(payload.teacherId || '').trim() || '선생님'),
    '학교: ' + payload.schoolName,
    '학년/학기: ' + payload.grade + ' / ' + payload.semester,
    '대상연도: ' + payload.yearsText,
    '시험구분: ' + payload.examType,
    '문항수: ' + payload.count,
    '',
    String(text || '').trim()
  ].join('\n');
}

function savePastExamProblemsText_(targetSheetName, payload, text) {
  const ownerName = getGeneralProblemsOwnerName_(targetSheetName, payload);
  const dateText = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyyMMdd');
  const fileName = sanitizeFileName_(ownerName + '_' + dateText + '_기출유사문제.txt');
  return saveTextToNamedFolder_(ownerName, fileName, text);
}

function handlePastExamAnalysis_(payload) {
  const ss = SpreadsheetApp.getActive();
  const registrationSheet = ss.getSheetByName(SHEETS.PAST_EXAM_REGISTRATION);
  if (!registrationSheet) throw new Error('기출시험지등록 시트가 없습니다.');
  const registrationHeaders = getHeaderMap_(registrationSheet);
  const registrationRow = Number(payload.registrationRow || 0);
  if (!registrationRow) throw new Error('기출시험지등록 대상 행이 없습니다.');

  setRowValues_(registrationSheet, registrationRow, registrationHeaders, {
    '처리상태': 'RUNNING',
    '오류메시지': '',
    '마지막처리시간': new Date()
  });

  setupPastExamBankSheet();
  const fileParts = buildPastExamAnalysisFileParts_(payload);
  const problemNumbers = detectPastExamProblemNumbers_(payload, fileParts);
  if (!problemNumbers.length) {
    throw new Error('PDF에서 문제번호를 찾지 못했습니다.');
  }

  const analyzed = [];
  chunkByMaxSize_(problemNumbers, 5).forEach(numberChunk => {
    const prompt = buildPastExamAnalysisPrompt_(payload, numberChunk);
    const response = callGemini_(
      TASK_TYPES.PAST_EXAM_ANALYSIS,
      SHEETS.PAST_EXAM_REGISTRATION,
      prompt,
      fileParts
    );
    const items = parseJsonArray_(response.text);
    items.forEach(item => analyzed.push(normalizePastExamAnalysisItem_(item)));
  });

  const requested = {};
  problemNumbers.forEach(number => requested[normalizeProblemNumber_(number)] = true);
  const normalizedItems = analyzed.filter(item => requested[item.problemNumber]);
  const found = {};
  normalizedItems.forEach(item => found[item.problemNumber] = true);
  const missing = problemNumbers.filter(number => !found[normalizeProblemNumber_(number)]);
  if (missing.length) {
    throw new Error('AI 분석 결과에서 누락된 문제번호: ' + missing.join(', '));
  }

  const savedCount = upsertPastExamBankRows_(payload, normalizedItems);
  setRowValues_(registrationSheet, registrationRow, registrationHeaders, {
    '처리상태': 'DONE',
    '오류메시지': '',
    '등록문항수': savedCount,
    '마지막처리시간': new Date()
  });
}

function buildPastExamAnalysisFileParts_(payload) {
  const parts = [buildGeminiFilePart_(payload.examPdfUrl)];
  if (payload.answerPdfUrl && payload.answerPdfUrl !== payload.examPdfUrl) {
    parts.push(buildGeminiFilePart_(payload.answerPdfUrl));
  }
  return parts;
}

function detectPastExamProblemNumbers_(payload, fileParts) {
  const prompt = [
    '첨부된 중학교 수학 시험 PDF를 확인하라.',
    '학교: ' + payload.schoolName,
    '연도: ' + payload.year,
    '학년: ' + payload.grade,
    '학기: ' + payload.semester,
    '시험구분: ' + payload.examType,
    '',
    '시험문제의 최상위 문제번호만 순서대로 찾아라.',
    '예를 들어 9번 안에 (1-1), (1-2)가 있으면 최상위 번호 9 하나만 반환하라.',
    'PDF 뒤쪽에 답지와 해설지가 함께 있어도 답지의 번호를 중복해서 세지 마라.',
    '문제지에 실제로 출제된 번호만 반환하라.',
    '반드시 JSON 배열만 출력하라.',
    '형식: [{"problemNumber":"1"},{"problemNumber":"2"}]'
  ].join('\n');

  const response = callGemini_(
    TASK_TYPES.PAST_EXAM_ANALYSIS,
    SHEETS.PAST_EXAM_REGISTRATION,
    prompt,
    fileParts
  );
  return unique_(parseJsonArray_(response.text)
    .map(item => normalizeProblemNumber_(item.problemNumber || item.number))
    .filter(Boolean))
    .sort(compareProblemNumbers_);
}

function buildPastExamAnalysisPrompt_(payload, problemNumbers) {
  const answerSourceInstruction = payload.answerPdfUrl
    ? '첫 번째 첨부는 시험지이고 두 번째 첨부는 정답 또는 해설 자료다.'
    : '한 PDF 안에 시험지와 답지 또는 해설지가 함께 있을 수 있다. 앞뒤 페이지를 모두 확인하라.';

  return [
    '너는 중학교 수학 시험지를 정확하게 전산화하는 분석 교사다.',
    '각 번호의 문제지 본문과 뒤쪽 정답·해설을 번호 기준으로 매칭하라.',
    '문항 안의 소문항은 최상위 문제 한 개의 problemText와 solution 안에 함께 보존하라.',
    '문제본문은 보기, 조건, 배점, 소문항을 포함하되 학교명 머리글과 페이지 번호는 제외하라.',
    '정답은 답지에 적힌 값을 우선 사용하고 해설 계산과 일치하는지 확인하라.',
    '상위단원, 하위단원, 문제유형은 해당 문제를 실제로 푸는 핵심 개념 기준으로 구체적으로 작성하라.',
    '난이도는 하, 중, 상 중 하나만 사용하라.',
    '도형이나 그래프가 있으면 hasImage를 true로 하고 imageDescription에 후처리 프로그램이 다시 그릴 수 있도록 점, 선, 좌표, 길이, 각도, 식, 배치를 구체적으로 작성하라.',
    '도형이나 그래프가 없으면 hasImage는 false, imageDescription은 빈 문자열로 작성하라.',
    '문제본문에서 이미지가 필요한 위치에는 반드시 [이미지 필요: 구체적 설명] 형식만 사용하라.',
    '[그림 필요: ...] 표현은 절대 사용하지 마라.',
    '수식은 LaTeX 명령어 대신 일반 텍스트와 유니코드 기호로 작성하라.',
    'confidence는 HIGH, MEDIUM, LOW 중 하나만 사용하라.',
    '판독이나 답지 매칭이 불확실하면 reviewMemo에 이유를 기록하라.',
    '요청하지 않은 문제번호는 출력하지 마라.',
    '반드시 JSON 배열만 출력하고 마크다운과 코드블록을 쓰지 마라.',
    '',
    '형식:',
    '[{"problemNumber":"1","problemText":"...","answer":"...","solution":"...","unit1":"...","unit2":"...","problemType":"...","difficulty":"중","hasImage":false,"imageDescription":"","confidence":"HIGH","reviewMemo":""}]',
    '',
    '이번 요청 데이터:',
    answerSourceInstruction,
    '학교: ' + payload.schoolName,
    '연도: ' + payload.year,
    '학년: ' + payload.grade,
    '학기: ' + payload.semester,
    '시험구분: ' + payload.examType,
    '이번에 분석할 최상위 문제번호: ' + problemNumbers.join(', ')
  ].join('\n');
}

function normalizePastExamAnalysisItem_(item) {
  return {
    problemNumber: normalizeProblemNumber_(item.problemNumber || item.number),
    problemText: String(item.problemText || item.problem || '').trim(),
    answer: String(item.answer || '').trim(),
    solution: String(item.solution || item.explanation || '').trim(),
    unit1: String(item.unit1 || '').trim(),
    unit2: String(item.unit2 || '').trim(),
    problemType: String(item.problemType || item.type || '').trim(),
    difficulty: normalizePastExamDifficulty_(item.difficulty),
    hasImage: normalizeBooleanText_(item.hasImage),
    imageDescription: String(item.imageDescription || '').trim(),
    confidence: normalizeConfidence_(item.confidence),
    reviewMemo: String(item.reviewMemo || item.reviewReason || '').trim()
  };
}

function normalizePastExamDifficulty_(value) {
  const text = String(value || '').trim();
  if (text === '상' || text === '하') return text;
  return '중';
}

function normalizeBooleanText_(value) {
  if (value === true || String(value).toUpperCase() === 'TRUE') return 'TRUE';
  return 'FALSE';
}

function upsertPastExamBankRows_(payload, items) {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, SHEETS.PAST_EXAM_BANK, HEADERS.PAST_EXAM_BANK);
  ensureBankReviewWorkflow_(sheet);
  const headers = getHeaderMap_(sheet);
  const existing = {};

  readObjects_(sheet).forEach(entry => {
    existing[buildPastExamBankKey_(
      entry.rowObject['학교명'],
      entry.rowObject['연도'],
      entry.rowObject['학년'],
      entry.rowObject['학기'],
      entry.rowObject['시험구분'],
      entry.rowObject['문제번호']
    )] = entry.rowNumber;
  });

  let savedCount = 0;
  items
    .filter(item => item.problemNumber)
    .sort((a, b) => compareProblemNumbers_(a.problemNumber, b.problemNumber))
    .forEach(item => {
      const values = {
        '학교명': payload.schoolName,
        '연도': payload.year,
        '학년': payload.grade,
        '학기': payload.semester,
        '시험구분': payload.examType,
        '문제번호': item.problemNumber,
        '문제본문': item.problemText,
        '정답': item.answer,
        '해설': item.solution,
        '상위단원': item.unit1,
        '하위단원': item.unit2,
        '문제유형': item.problemType,
        '난이도': item.difficulty,
        '도형그래프포함여부': item.hasImage,
        '이미지링크': '',
        '이미지설명': item.imageDescription,
        '이미지템플릿': '',
        '이미지필수항목': '',
        '기출이미지ID': '',
        '원본PDF링크': payload.examPdfUrl,
        '신뢰도': item.confidence,
        '검산메모': item.reviewMemo,
        '처리상태': item.confidence === 'HIGH' && !item.reviewMemo ? 'DONE' : 'REVIEW',
        '사용여부': 'TRUE'
      };
      const key = buildPastExamBankKey_(
        payload.schoolName,
        payload.year,
        payload.grade,
        payload.semester,
        payload.examType,
        item.problemNumber
      );
      const rowNumber = existing[key] || sheet.getLastRow() + 1;
      if (!existing[key]) existing[key] = rowNumber;
      setRowValues_(sheet, rowNumber, headers, values);
      savedCount += 1;
    });
  return savedCount;
}

function buildPastExamBankKey_(schoolName, year, grade, semester, examType, problemNumber) {
  return [
    String(schoolName || '').trim(),
    normalizeYear_(year),
    normalizeComparableText_(grade),
    normalizeComparableText_(semester),
    normalizeComparableText_(examType),
    normalizeProblemNumber_(problemNumber)
  ].join('||');
}

function tryMarkTeacherTaskError_(targetSheetName, targetRow, payloadJson, err) {
  try {
    const payload = JSON.parse(payloadJson || '{}');
    const sheet = getTeacherSheetForTask_(targetSheetName, payload);
    if (!sheet) return;
    const headers = getHeaderMap_(sheet);
    setRowValues_(sheet, targetRow, headers, {
      '처리상태': 'ERROR',
      '오류메시지': String(err && err.message ? err.message : err).slice(0, 1000)
    });
  } catch (ignored) {
    // 큐 오류 기록이 우선입니다.
  }
}

function tryMarkTeacherTaskRetry_(targetSheetName, targetRow, payloadJson, err) {
  try {
    const payload = JSON.parse(payloadJson || '{}');
    const sheet = getTeacherSheetForTask_(targetSheetName, payload);
    if (!sheet) return;
    const headers = getHeaderMap_(sheet);
    const taskType = String(payload.taskType || '');
    const completed = Number(
      payload.pastExamGeneratedCount
      || payload.generalGeneratedCount
      || payload.twinGeneratedCount
      || 0
    );
    const total = Number(payload.count || 0);
    const progressText = completed > 0 && total > 0
      ? 'RUNNING ' + completed + '/' + total
      : 'RUNNING';
    setRowValues_(sheet, targetRow, headers, {
      '처리상태': progressText,
      '오류메시지': '자동 재시도 예약: ' + String(err && err.message ? err.message : err).slice(0, 900)
    });
  } catch (ignored) {
    // 큐 오류 기록이 우선입니다.
  }
}
