/**
 * 선생님별 파일용 Apps Script 뼈대.
 *
 * 역할:
 * - 선생님이 입력한 행을 읽음
 * - 중앙 마스터 스프레드시트 작업큐에 요청을 넣음
 * - 처리상태/오류메세지만 즉시 갱신
 */

const MASTER_SPREADSHEET_ID = 'TODO_MASTER_SPREADSHEET_ID';

const TEACHER_SHEET_NAME = '오답노트';
const WRONG_INPUT_SHEET_NAME = '오답입력기';

const MASTER_SHEETS = {
  PROBLEM_BANK: '문제은행',
  QUEUE: '작업큐',
};

const MASTER_HEADERS = {
  QUEUE: ['작업ID', '작업종류', '대상시트', '대상행', '상태', '재시도횟수', '예약시각', '오류메시지', '생성시간', '처리시간', '페이로드JSON'],
};

const QUEUE_STATUS = {
  PENDING: 'PENDING',
  RUNNING: 'RUNNING',
};

const TEACHER_HEADERS = [
  '학생 이름',
  '시험지 이름',
  '틀린 문제 번호',
  '분석 보고서',
  '쌍둥이 문항',
  '누적 분석 보고서',
  '처리상태',
  '오류메시지',
];

const WRONG_INPUT_FIXED_HEADERS = {
  EXAM_LABEL_CELL: 'A1',
  EXAM_VALUE_CELL: 'B1',
  STUDENT_HEADER_CELL: 'B2',
};

const WRONG_INPUT_PROBLEM_START_COLUMN = 3;
const WRONG_INPUT_STUDENT_NAME_COLUMN = 2;
const PERFECT_SCORE_TEXT = '오답 없음 (100점)';

const TEACHER_TASK_TYPES = {
  STUDENT_REPORT: 'STUDENT_REPORT',
  SIMILAR_PROBLEMS: 'SIMILAR_PROBLEMS',
  CUMULATIVE_REPORT: 'CUMULATIVE_REPORT',
};

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('오답노트 생성기')
    .addItem('선생님 파일 전체 셋업', 'setupTeacherWorkbook')
    .addItem('오답입력기 초기화', 'setupWrongInputSheet')
    .addItem('오답입력기 리셋', 'resetWrongInputSheet')
    .addItem('오답입력기 문제번호 불러오기', 'loadWrongInputProblemNumbers')
    .addItem('오답입력기 체크 오답 입력', 'submitWrongInputSelections')
    .addSeparator()
    .addItem('선택 행 분석보고서 요청', 'requestAnalysisReportForSelectedRows')
    .addItem('선택 행 쌍둥이문항 요청', 'requestTwinProblemsForSelectedRows')
    .addItem('선택 행 누적분석보고서 요청', 'requestCumulativeReportForSelectedRows')
    .addSeparator()
    .addItem('선택 행 전체 처리 요청', 'requestAllForSelectedRows')
    .addToUi();
}

function setupTeacherWorkbook() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const outputSheet = getOrCreateSheet_(ss, TEACHER_SHEET_NAME);
  ensureHeaderRow_(outputSheet, TEACHER_HEADERS);
  formatTeacherOutputSheet_(outputSheet);
  setupWrongInputSheet();
}

function setupWrongInputSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = getOrCreateSheet_(ss, WRONG_INPUT_SHEET_NAME);

  sheet.getRange(WRONG_INPUT_FIXED_HEADERS.EXAM_LABEL_CELL).setValue('시험지 이름');
  sheet.getRange(WRONG_INPUT_FIXED_HEADERS.STUDENT_HEADER_CELL).setValue('학생 이름');
  if (sheet.getRange('A2').getValue() === '학생 이름') {
    sheet.getRange('A2').clearContent();
  }
  if (!sheet.getRange(WRONG_INPUT_FIXED_HEADERS.EXAM_VALUE_CELL).getValue()) {
    sheet.getRange(WRONG_INPUT_FIXED_HEADERS.EXAM_VALUE_CELL).setValue('');
  }
  if (sheet.getRange('B2').getValue() === '문제번호를 불러오세요') {
    sheet.getRange('B2').clearContent();
  }
  if (!sheet.getRange('C2').getValue()) {
    sheet.getRange('C2').setValue('문제번호를 불러오세요');
  }

  formatWrongInputSheet_(sheet);
}

function resetWrongInputSheet() {
  const sheet = getWrongInputSheet_();
  const examName = sheet.getRange('B1').getValue();
  sheet.clear();
  sheet.getRange('B1').setValue(examName);
  setupWrongInputSheet();
}

function formatTeacherOutputSheet_(sheet) {
  sheet.setFrozenRows(1);
  sheet.getRange(1, 1, 1, TEACHER_HEADERS.length).setFontWeight('bold').setBackground('#f1f3f4');
  sheet.autoResizeColumns(1, TEACHER_HEADERS.length);
}

function formatWrongInputSheet_(sheet) {
  sheet.setFrozenRows(2);
  sheet.setFrozenColumns(2);
  sheet.getRange('A1:B2').setFontWeight('bold').setBackground('#f1f3f4');
  sheet.getRange('C2:2').setFontWeight('bold').setBackground('#f8f9fa');
  sheet.autoResizeColumn(1);
  sheet.setColumnWidth(2, 140);
}

function resizeProblemNumberColumns_(sheet, problemNumbers) {
  const width = 42;
  for (let index = 0; index < problemNumbers.length; index += 1) {
    sheet.setColumnWidth(WRONG_INPUT_PROBLEM_START_COLUMN + index, width);
  }
}

function loadWrongInputProblemNumbers() {
  const sheet = getWrongInputSheet_();
  const examName = String(sheet.getRange('B1').getValue() || '').trim();
  if (!examName) throw new Error('오답입력기 B1에 시험지 이름을 입력하세요.');

  const problemNumbers = fetchProblemNumbersFromMaster_(examName);
  if (!problemNumbers.length) throw new Error('중앙 문제은행에서 문제번호를 찾지 못했습니다: ' + examName);

  const lastColumn = Math.max(sheet.getLastColumn(), 2);
  const maxRows = Math.max(sheet.getMaxRows() - 2, 1);
  sheet.getRange(3, 2, maxRows, 1).removeCheckboxes();
  sheet.getRange(2, WRONG_INPUT_PROBLEM_START_COLUMN, 1, lastColumn - WRONG_INPUT_PROBLEM_START_COLUMN + 1).clearContent();
  sheet.getRange(3, WRONG_INPUT_PROBLEM_START_COLUMN, maxRows, lastColumn - WRONG_INPUT_PROBLEM_START_COLUMN + 1).clearContent().removeCheckboxes();

  sheet.getRange(2, WRONG_INPUT_PROBLEM_START_COLUMN, 1, problemNumbers.length).setValues([problemNumbers]);
  sheet.getRange(3, WRONG_INPUT_PROBLEM_START_COLUMN, maxRows, problemNumbers.length).insertCheckboxes();
  sheet
    .getRange(2, WRONG_INPUT_PROBLEM_START_COLUMN, maxRows + 1, problemNumbers.length)
    .setHorizontalAlignment('center')
    .setVerticalAlignment('middle');
  resizeProblemNumberColumns_(sheet, problemNumbers);
}

function submitWrongInputSelections() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const inputSheet = getWrongInputSheet_();
  const outputSheet = getTeacherOutputSheet_();
  ensureHeaderRow_(outputSheet, TEACHER_HEADERS);

  const examName = String(inputSheet.getRange('B1').getValue() || '').trim();
  if (!examName) throw new Error('오답입력기 B1에 시험지 이름을 입력하세요.');

  const lastRow = inputSheet.getLastRow();
  const lastColumn = inputSheet.getLastColumn();
  if (lastRow < 3 || lastColumn < WRONG_INPUT_PROBLEM_START_COLUMN) throw new Error('입력할 학생/문제번호가 없습니다.');

  const problemNumbers = inputSheet.getRange(2, WRONG_INPUT_PROBLEM_START_COLUMN, 1, lastColumn - WRONG_INPUT_PROBLEM_START_COLUMN + 1).getValues()[0]
    .map((value) => String(value || '').trim())
    .filter(Boolean);
  if (!problemNumbers.length) throw new Error('문제번호를 먼저 불러오세요.');

  const studentNames = inputSheet.getRange(3, WRONG_INPUT_STUDENT_NAME_COLUMN, lastRow - 2, 1).getValues()
    .map((row) => String(row[0] || '').trim());
  const checkedGrid = inputSheet.getRange(3, WRONG_INPUT_PROBLEM_START_COLUMN, lastRow - 2, problemNumbers.length).getValues();
  const rowsToAppend = [];

  checkedGrid.forEach((row, rowIndex) => {
    const studentName = studentNames[rowIndex];
    if (!studentName) return;

    const wrongNumbers = row
      .map((checked, columnIndex) => checked === true ? problemNumbers[columnIndex] : '')
      .filter(Boolean);

    rowsToAppend.push(buildTeacherOutputRow_(
      studentName,
      examName,
      wrongNumbers.length ? wrongNumbers.join(', ') : PERFECT_SCORE_TEXT
    ));
  });

  if (!rowsToAppend.length) throw new Error('입력할 학생 이름이 없습니다.');

  outputSheet
    .getRange(outputSheet.getLastRow() + 1, 1, rowsToAppend.length, TEACHER_HEADERS.length)
    .setValues(rowsToAppend);
}

function requestAnalysisReportForSelectedRows() {
  enqueueSelectedRows_(TEACHER_TASK_TYPES.STUDENT_REPORT, '분석보고서대기');
}

function requestTwinProblemsForSelectedRows() {
  enqueueSelectedRows_(TEACHER_TASK_TYPES.SIMILAR_PROBLEMS, '쌍둥이대기');
}

function requestCumulativeReportForSelectedRows() {
  enqueueSelectedRows_(TEACHER_TASK_TYPES.CUMULATIVE_REPORT, '누적분석대기');
}

function requestAllForSelectedRows() {
  enqueueSelectedRows_(TEACHER_TASK_TYPES.STUDENT_REPORT, '분석보고서대기');
  enqueueSelectedRows_(TEACHER_TASK_TYPES.SIMILAR_PROBLEMS, '쌍둥이대기');
  enqueueSelectedRows_(TEACHER_TASK_TYPES.CUMULATIVE_REPORT, '누적분석대기');
}

function enqueueSelectedRows_(taskType, queuedStatus) {
  const sheet = SpreadsheetApp.getActiveSheet();
  const range = sheet.getActiveRange();
  if (!range) throw new Error('요청할 행을 먼저 선택하세요.');

  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const startRow = range.getRow();
  const endRow = startRow + range.getNumRows() - 1;

  for (let row = Math.max(startRow, 2); row <= endRow; row += 1) {
    const payload = buildPayloadFromRow_(sheet, headers, row, taskType);
    validatePayload_(payload);
    enqueueToMaster_(payload);
    setCellByHeader_(sheet, headers, row, '처리상태', queuedStatus);
    setCellByHeader_(sheet, headers, row, '오류메시지', '');
  }
}

function buildPayloadFromRow_(sheet, headers, row, taskType) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  return {
    taskType,
    teacherId: getTeacherId_(),
    teacherFileId: ss.getId(),
    teacherSheetName: sheet.getName(),
    teacherRow: row,
    studentName: getCellByHeader_(sheet, headers, row, '학생 이름'),
    examName: getCellByHeader_(sheet, headers, row, '시험지 이름'),
    wrongNumbers: getCellByHeader_(sheet, headers, row, '틀린 문제 번호'),
    requestedAt: new Date().toISOString(),
  };
}

function validatePayload_(payload) {
  if (!payload.studentName) throw new Error('학생 이름이 비어 있습니다.');
  if (!payload.examName) throw new Error('시험지 이름이 비어 있습니다.');
  if (!payload.wrongNumbers) throw new Error('틀린 문제 번호가 비어 있습니다.');
}

function enqueueToMaster_(payload) {
  const master = getMasterSpreadsheet_();
  const queueSheet = getOrCreateMasterSheet_(master, MASTER_SHEETS.QUEUE, MASTER_HEADERS.QUEUE);
  ensureHeaderRow_(queueSheet, MASTER_HEADERS.QUEUE);

  const normalizedPayload = normalizeQueuePayload_(payload);
  const targetSheet = buildRemoteTeacherTargetSheet_(normalizedPayload);
  const existingTaskId = findOpenQueueTask_(queueSheet, normalizedPayload.taskType, targetSheet, normalizedPayload.teacherRow, normalizedPayload);
  if (existingTaskId) return existingTaskId;

  const now = new Date();
  const taskId = Utilities.getUuid();
  queueSheet.getRange(queueSheet.getLastRow() + 1, 1, 1, MASTER_HEADERS.QUEUE.length).setValues([[
    taskId,
    normalizedPayload.taskType,
    targetSheet,
    normalizedPayload.teacherRow,
    QUEUE_STATUS.PENDING,
    0,
    now,
    '',
    now,
    '',
    JSON.stringify(normalizedPayload),
  ]]);
  return taskId;
}

function fetchProblemNumbersFromMaster_(examName) {
  const master = getMasterSpreadsheet_();
  const problemSheet = master.getSheetByName(MASTER_SHEETS.PROBLEM_BANK);
  if (!problemSheet) throw new Error('중앙 마스터에 문제은행 시트가 없습니다.');

  const rows = readSheetAsObjects_(problemSheet);
  return unique_(rows
    .filter((row) => String(row['시험지 이름'] || '').trim() === String(examName || '').trim())
    .map((row) => normalizeProblemNumber_(row['문제번호']))
    .filter(Boolean))
    .sort(compareProblemNumbers_);
}

function setMasterSpreadsheetId(idOrUrl) {
  const id = extractSpreadsheetId_(idOrUrl);
  if (!id) throw new Error('중앙 마스터 스프레드시트 ID 또는 URL이 올바르지 않습니다.');
  PropertiesService.getDocumentProperties().setProperty('MASTER_SPREADSHEET_ID', id);
}

function setMasterOnce() {
  setMasterSpreadsheetId('https://docs.google.com/spreadsheets/d/1yWyl-YdV_k87mUDA-3ohdxzq0YJ0H1vM0BIK56QGoFE/edit?usp=sharing');
}

function checkSavedMasterSpreadsheetId() {
  SpreadsheetApp.getUi().alert(getMasterSpreadsheetId_());
}

function testMasterConnection() {
  const master = getMasterSpreadsheet_();
  SpreadsheetApp.getUi().alert('연결 성공: ' + master.getName());
}

function getMasterSpreadsheet_() {
  return SpreadsheetApp.openById(getMasterSpreadsheetId_());
}

function getMasterSpreadsheetId_() {
  return PropertiesService.getDocumentProperties().getProperty('MASTER_SPREADSHEET_ID') || MASTER_SPREADSHEET_ID;
}

function normalizeQueuePayload_(payload) {
  const normalized = Object.assign({}, payload);
  if (normalized.taskType === TEACHER_TASK_TYPES.CUMULATIVE_REPORT) {
    normalized.taskType = TEACHER_TASK_TYPES.STUDENT_REPORT;
  }
  normalized.wrongNumbersText = String(normalized.wrongNumbers || normalized.wrongNumbersText || '').trim();
  return normalized;
}

function buildRemoteTeacherTargetSheet_(payload) {
  return ['REMOTE', payload.teacherFileId, payload.teacherSheetName].join('::');
}

function findOpenQueueTask_(queueSheet, taskType, targetSheet, targetRow, payload) {
  const rows = readSheetAsObjects_(queueSheet);
  const targetKey = [taskType, payload.teacherFileId, payload.teacherSheetName, targetRow].join('||');
  for (let i = 0; i < rows.length; i += 1) {
    const row = rows[i];
    if (row['상태'] !== QUEUE_STATUS.PENDING && row['상태'] !== QUEUE_STATUS.RUNNING) continue;
    let rowPayload = {};
    try {
      rowPayload = JSON.parse(row['페이로드JSON'] || '{}');
    } catch (err) {
      rowPayload = {};
    }
    const rowKey = [row['작업종류'], rowPayload.teacherFileId, rowPayload.teacherSheetName, row['대상행']].join('||');
    if (rowKey === targetKey) return row['작업ID'];
  }
  return '';
}

function getWrongInputSheet_() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(WRONG_INPUT_SHEET_NAME);
  if (!sheet) throw new Error('오답입력기 시트가 없습니다. setupWrongInputSheet()를 먼저 실행하세요.');
  return sheet;
}

function getTeacherOutputSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  return getOrCreateSheet_(ss, TEACHER_SHEET_NAME);
}

function buildTeacherOutputRow_(studentName, examName, wrongNumbersText) {
  const valuesByHeader = {
    '학생 이름': studentName,
    '시험지 이름': examName,
    '틀린 문제 번호': wrongNumbersText,
    '처리상태': '오답입력완료',
    '오류메시지': '',
  };
  return TEACHER_HEADERS.map((header) => valuesByHeader[header] || '');
}

function getTeacherId_() {
  const properties = PropertiesService.getDocumentProperties();
  let teacherId = properties.getProperty('TEACHER_ID');
  if (!teacherId) {
    teacherId = SpreadsheetApp.getActiveSpreadsheet().getName();
    properties.setProperty('TEACHER_ID', teacherId);
  }
  return teacherId;
}

function ensureHeaderRow_(sheet, headers) {
  const current = sheet.getRange(1, 1, 1, headers.length).getValues()[0];
  const hasAnyHeader = current.some((value) => value !== '');
  if (!hasAnyHeader) {
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  }
}

function getCellByHeader_(sheet, headers, row, headerName) {
  const columnIndex = headers.indexOf(headerName);
  if (columnIndex === -1) throw new Error('컬럼이 없습니다: ' + headerName);
  return sheet.getRange(row, columnIndex + 1).getValue();
}

function setCellByHeader_(sheet, headers, row, headerName, value) {
  const columnIndex = headers.indexOf(headerName);
  if (columnIndex !== -1) {
    sheet.getRange(row, columnIndex + 1).setValue(value);
  }
}

function getOrCreateSheet_(ss, name) {
  return ss.getSheetByName(name) || ss.insertSheet(name);
}

function getOrCreateMasterSheet_(ss, name, headers) {
  const sheet = ss.getSheetByName(name) || ss.insertSheet(name);
  if (headers) ensureHeaderRow_(sheet, headers);
  return sheet;
}

function readSheetAsObjects_(sheet) {
  const values = sheet.getDataRange().getValues();
  if (values.length < 2) return [];
  const headers = values[0].map((header) => String(header || '').trim());
  return values.slice(1).map((row) => {
    const object = {};
    headers.forEach((header, index) => {
      object[header] = row[index];
    });
    return object;
  });
}

function extractSpreadsheetId_(idOrUrl) {
  const text = String(idOrUrl || '').trim();
  const match = text.match(/\/spreadsheets\/d\/([a-zA-Z0-9-_]+)/);
  if (match) return match[1];
  if (/^[a-zA-Z0-9-_]{20,}$/.test(text)) return text;
  return '';
}

function normalizeProblemNumber_(value) {
  return String(value || '').trim();
}

function compareProblemNumbers_(a, b) {
  const left = Number(String(a).match(/\d+/));
  const right = Number(String(b).match(/\d+/));
  if (!isNaN(left) && !isNaN(right) && left !== right) return left - right;
  return String(a).localeCompare(String(b), 'ko');
}

function unique_(values) {
  const seen = {};
  return values.filter((value) => {
    if (!value || seen[value]) return false;
    seen[value] = true;
    return true;
  });
}
