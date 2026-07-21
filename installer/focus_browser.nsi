; Copyright 2025 The Focus Authors
; You can use, redistribute, and/or modify this source code under
; the terms of the GPL-3.0 license that can be found in the LICENSE file.
;
; NSIS installer script for Focus Browser.
; Wraps setup.exe + focus_browser.7z with a GUI.
;
; Required defines (passed via makensis -D):
;   VERSION    - Four-part Windows version (e.g., 1.0.0.0)
;   DISPLAY_VERSION - User-facing release version (e.g., 1.0)
;   ARCH       - Target architecture (x64 or arm64)
;   SETUP_EXE  - Path to setup.exe
;   FOCUS_BROWSER_7Z - Path to focus_browser.7z
;   ICON_FILE  - Path to Focus Browser .ico file
;   OUTPUT_FILE - Output installer .exe path
;   LICENSE_FILE - Path to LICENSE file

!include "MUI2.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

; Keep manual makensis invocations compatible when DISPLAY_VERSION is omitted.
!ifndef DISPLAY_VERSION
!define DISPLAY_VERSION "${VERSION}"
!endif

; --- Product Information ---
!define PRODUCT_NAME "Focus Browser"
!define PRODUCT_PUBLISHER "Focus Browser"
!define PRODUCT_COMPANY_PATH "FocusBrowser"
!define PRODUCT_GUID "{B4433AC8-0E88-481E-BAA5-D88689C59436}"

; --- Installer Configuration ---
Name "${PRODUCT_NAME} ${DISPLAY_VERSION}"
OutFile "${OUTPUT_FILE}"
Unicode true
ManifestDPIAware true
SetCompress off
RequestExecutionLevel user
ShowInstDetails show

; --- Variables ---
Var InstallType
Var SetupExitCode
Var SetupFlags
Var InstallFailed
Var WrapperExitCode
Var RadioUser
Var RadioSystem
Var SystemInstallExists

; --- MUI2 Configuration ---
!define MUI_ICON "${ICON_FILE}"
!define MUI_ABORTWARNING
!define MUI_ABORTWARNING_TEXT "Прервать установку ${PRODUCT_NAME}?"

; Welcome page
!define MUI_WELCOMEPAGE_TITLE "Установка ${PRODUCT_NAME}"
!define MUI_WELCOMEPAGE_TEXT "Мастер установит ${PRODUCT_NAME} ${DISPLAY_VERSION} на этот компьютер.$\r$\n$\r$\nНажмите «Далее», чтобы продолжить."
!insertmacro MUI_PAGE_WELCOME

; License page
!insertmacro MUI_PAGE_LICENSE "${LICENSE_FILE}"

; Install type selection (custom page)
Page custom InstallTypePage InstallTypePageLeave

; Installation progress
!insertmacro MUI_PAGE_INSTFILES

; Finish page
!define MUI_FINISHPAGE_TITLE "${PRODUCT_NAME} установлен"
!define MUI_FINISHPAGE_TEXT "${PRODUCT_NAME} успешно установлен.$\r$\n$\r$\nНажмите «Готово», чтобы закрыть мастер установки."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Запустить ${PRODUCT_NAME}"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchFocusBrowser
!insertmacro MUI_PAGE_FINISH

; --- Language ---
!insertmacro MUI_LANGUAGE "Russian"

; --- Version Information ---
VIProductVersion "${VERSION}"
VIAddVersionKey /LANG=1049 "ProductName" "${PRODUCT_NAME}"
VIAddVersionKey /LANG=1049 "CompanyName" "${PRODUCT_PUBLISHER}"
VIAddVersionKey /LANG=1049 "FileDescription" "${PRODUCT_NAME} Installer"
VIAddVersionKey /LANG=1049 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1049 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1049 "LegalCopyright" "Copyright Focus Browser"

; =============================================================================
; Custom Install Type Page
; =============================================================================

Function InstallTypePage
  !insertmacro MUI_HEADER_TEXT "Тип установки" "Выберите, для кого установить ${PRODUCT_NAME}."

  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    MessageBox MB_OK|MB_ICONSTOP "Не удалось открыть страницу выбора установки." /SD IDOK
    SetErrorLevel 7
    Quit
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 24u "Выберите тип установки:"
  Pop $0

  ${NSD_CreateRadioButton} 10u 30u 280u 12u "Только для текущего пользователя (рекомендуется)"
  Pop $RadioUser

  ${NSD_CreateLabel} 24u 44u 280u 16u "Установка в профиль пользователя. Права администратора не требуются."
  Pop $0

  ${NSD_CreateRadioButton} 10u 66u 280u 12u "Для всех пользователей"
  Pop $RadioSystem

  ${NSD_CreateLabel} 24u 80u 280u 16u "Системная установка. Потребуются права администратора."
  Pop $0

  ${If} $SystemInstallExists == "1"
    ; Disable per-user option and force system install when a system-wide installation already exists
    EnableWindow $RadioUser 0
    ${NSD_SetState} $RadioSystem ${BST_CHECKED}
    ${NSD_CreateLabel} 10u 104u 300u 16u "Обнаружена системная установка. Выбран режим для всех пользователей."
    Pop $0
  ${Else}
    ${If} $InstallType == "system"
      ${NSD_SetState} $RadioSystem ${BST_CHECKED}
    ${Else}
      ${NSD_SetState} $RadioUser ${BST_CHECKED}
    ${EndIf}
  ${EndIf}

  nsDialogs::Show
FunctionEnd

Function InstallTypePageLeave
  ${NSD_GetState} $RadioUser $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $InstallType "user"
  ${Else}
    StrCpy $InstallType "system"
  ${EndIf}
FunctionEnd

; =============================================================================
; Initialization
; =============================================================================

Function .onInit
  ; Default to user install; parse command-line flags
  StrCpy $InstallType "user"
  StrCpy $SetupFlags ""
  ${GetParameters} $0

  ${GetOptions} $0 "/SYSTEM" $1
  ${IfNot} ${Errors}
    StrCpy $InstallType "system"
  ${EndIf}
  ClearErrors

  ${GetOptions} $0 "/VERBOSE-LOGGING" $1
  ${IfNot} ${Errors}
    StrCpy $SetupFlags '$SetupFlags --verbose-logging'
  ${EndIf}
  ClearErrors

  ${GetOptions} $0 "/LOG-FILE=" $1
  ${IfNot} ${Errors}
    StrCpy $SetupFlags '$SetupFlags --log-file="$1"'
  ${EndIf}
  ClearErrors

  ; Check for existing system-wide installation
  StrCpy $SystemInstallExists "0"
  ${If} ${FileExists} "$PROGRAMFILES64\${PRODUCT_COMPANY_PATH}\${PRODUCT_NAME}\Application\chrome.exe"
    StrCpy $SystemInstallExists "1"
    StrCpy $InstallType "system"
  ${EndIf}

  ; Check Windows version (setup.exe requires Windows 10+)
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_OK|MB_ICONSTOP "Для ${PRODUCT_NAME} требуется Windows 10 или новее." /SD IDOK
    SetErrorLevel 9
    Quit
  ${EndIf}

  ; Check architecture matches installer variant
  ${If} "${ARCH}" == "x64"
    ${IfNot} ${IsNativeAMD64}
      MessageBox MB_OK|MB_ICONSTOP "Этот установщик предназначен для систем x64. Загрузите версию для ARM64." /SD IDOK
      SetErrorLevel 54
      Quit
    ${EndIf}
  ${ElseIf} "${ARCH}" == "arm64"
    ${IfNot} ${IsNativeARM64}
      MessageBox MB_OK|MB_ICONSTOP "Этот установщик предназначен для систем ARM64. Загрузите версию для x64." /SD IDOK
      SetErrorLevel 54
      Quit
    ${EndIf}
  ${EndIf}
FunctionEnd

; =============================================================================
; Main Install Section
; =============================================================================

Section "Установка" SecInstall
  StrCpy $InstallFailed ""
  StrCpy $WrapperExitCode "0"

  ; Use NSIS' unique per-process plug-in directory. Never extract to, or
  ; recursively remove, a shared fixed directory under $TEMP.
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\focus_browser"
  DetailPrint "Распаковка установочных файлов..."
  File /oname=setup.exe "${SETUP_EXE}"
  File /oname=focus_browser.7z "${FOCUS_BROWSER_7Z}"

  ; Build setup.exe command line
  StrCpy $0 '"$PLUGINSDIR\focus_browser\setup.exe" --install-archive="$PLUGINSDIR\focus_browser\focus_browser.7z" --do-not-launch-chrome'

  ${If} $InstallType == "system"
    StrCpy $0 '$0 --system-level'
  ${EndIf}

  ; Append optional flags (verbose logging, log file)
  StrCpy $0 '$0$SetupFlags'

  ; Run setup.exe
  DetailPrint "Установка ${PRODUCT_NAME}..."
  SetDetailsPrint none
  nsExec::ExecToLog $0
  Pop $SetupExitCode
  SetDetailsPrint both

  ; Handle exit code
  ${Switch} $SetupExitCode
    ${Case} "0"
      ; FIRST_INSTALL_SUCCESS
      DetailPrint "Установка успешно завершена."
      ${Break}

    ${Case} "1"
      ; INSTALL_REPAIRED
      DetailPrint "Установка успешно восстановлена."
      ${Break}

    ${Case} "2"
      ; NEW_VERSION_UPDATED
      DetailPrint "${PRODUCT_NAME} успешно обновлён."
      ${Break}

    ${Case} "3"
      ; EXISTING_VERSION_LAUNCHED
      DetailPrint "Установленная версия ${PRODUCT_NAME} уже актуальна."
      ${Break}

    ${Case} "30"
      ; IN_USE_UPDATED
      DetailPrint "${PRODUCT_NAME} обновлён. Перезапустите браузер, чтобы применить изменения."
      ${Break}

    ${Case} "4"
      ; HIGHER_VERSION_EXISTS
      DetailPrint "Уже установлена более новая версия ${PRODUCT_NAME}."
      MessageBox MB_OK|MB_ICONEXCLAMATION "Уже установлена более новая версия ${PRODUCT_NAME}. Удалите её только если действительно хотите установить эту, более старую версию." /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode "4"
      ${Break}

    ${Case} "5"
      ; USER_LEVEL_INSTALL_EXISTS
      DetailPrint "Уже существует установка для текущего пользователя."
      MessageBox MB_OK|MB_ICONEXCLAMATION "${PRODUCT_NAME} уже установлен для текущего пользователя. Повторите установку без режима «для всех пользователей» либо сначала удалите текущую версию." /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode "5"
      ${Break}

    ${Case} "6"
      ; SYSTEM_LEVEL_INSTALL_EXISTS
      DetailPrint "Уже существует системная установка."
      MessageBox MB_OK|MB_ICONEXCLAMATION "${PRODUCT_NAME} уже установлен для всех пользователей. Запустите установщик с правами администратора." /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode "6"
      ${Break}

    ${Case} "7"
      ; INSTALL_FAILED
      DetailPrint "Не удалось установить ${PRODUCT_NAME}."
      MessageBox MB_OK|MB_ICONEXCLAMATION "Не удалось установить ${PRODUCT_NAME}. Закройте работающий браузер и повторите попытку." /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode "7"
      ${Break}

    ${Case} "9"
      ; OS_NOT_SUPPORTED
      DetailPrint "Эта версия Windows не поддерживается."
      MessageBox MB_OK|MB_ICONEXCLAMATION "Для ${PRODUCT_NAME} требуется Windows 10 или новее." /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode "9"
      ${Break}

    ${Case} "12"
      ; UNCOMPRESSION_FAILED
      DetailPrint "Не удалось распаковать архив установщика."
      MessageBox MB_OK|MB_ICONEXCLAMATION "Не удалось распаковать архив установщика. Возможно, файл повреждён — загрузите установщик заново." /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode "12"
      ${Break}

    ${Case} "13"
      ; INVALID_ARCHIVE
      DetailPrint "Архив установщика повреждён или имеет неверный формат."
      MessageBox MB_OK|MB_ICONEXCLAMATION "Архив установщика повреждён или имеет неверный формат. Загрузите установщик заново." /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode "13"
      ${Break}

    ${Case} "14"
      ; INSUFFICIENT_RIGHTS
      DetailPrint "Недостаточно прав для установки."
      MessageBox MB_OK|MB_ICONEXCLAMATION "Недостаточно прав для установки ${PRODUCT_NAME}.$\r$\n$\r$\nЗапустите установщик от имени администратора или выберите установку только для текущего пользователя." /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode "14"
      ${Break}

    ${Case} "28"
      ; INSTALL_DIR_IN_USE
      DetailPrint "Папка установки используется другим процессом."
      MessageBox MB_OK|MB_ICONEXCLAMATION "Закройте ${PRODUCT_NAME} и другие процессы, использующие папку браузера, затем повторите установку." /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode "28"
      ${Break}

    ${Case} "31"
      ; SAME_VERSION_REPAIR_FAILED
      DetailPrint "Не удалось восстановить установленную версию."
      MessageBox MB_OK|MB_ICONEXCLAMATION "Не удалось восстановить ${PRODUCT_NAME}, пока браузер работает. Полностью закройте его и повторите установку." /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode "31"
      ${Break}

    ${Case} "54"
      ; CPU_NOT_SUPPORTED
      DetailPrint "Архитектура процессора не поддерживается."
      MessageBox MB_OK|MB_ICONEXCLAMATION "Эта сборка ${PRODUCT_NAME} не поддерживает архитектуру данного компьютера." /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode "54"
      ${Break}

    ${Case} "60"
      ; SETUP_SINGLETON_ACQUISITION_FAILED
      DetailPrint "Уже выполняется другая установка."
      MessageBox MB_OK|MB_ICONEXCLAMATION "Уже выполняется другая установка ${PRODUCT_NAME}. Дождитесь её завершения и повторите попытку." /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode "60"
      ${Break}

    ${Case} "61"
      ; SETUP_SINGLETON_RELEASED
      DetailPrint "Установка была прервана другим процессом установки."
      MessageBox MB_OK|MB_ICONEXCLAMATION "Установка была прервана другим процессом. Дождитесь завершения другой установки ${PRODUCT_NAME} и повторите попытку." /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode "61"
      ${Break}

    ${Case} "error"
      DetailPrint "Не удалось запустить setup.exe."
      MessageBox MB_OK|MB_ICONEXCLAMATION "Не удалось запустить внутренний установщик. Возможно, загруженный файл повреждён." /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode "7"
      ${Break}

    ${Case} "timeout"
      DetailPrint "Превышено время ожидания установки."
      MessageBox MB_OK|MB_ICONEXCLAMATION "Превышено время ожидания установки. Закройте работающий браузер и повторите попытку." /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode "7"
      ${Break}

    ${Default}
      DetailPrint "Ошибка установки. Код setup.exe: $SetupExitCode"
      MessageBox MB_OK|MB_ICONEXCLAMATION "Не удалось установить ${PRODUCT_NAME}.$\r$\nКод ошибки setup.exe: $SetupExitCode" /SD IDOK
      StrCpy $InstallFailed "1"
      StrCpy $WrapperExitCode $SetupExitCode
      ${Break}

  ${EndSwitch}

  ; Remove only the two payload files extracted by this wrapper and their now
  ; empty per-process directory. Never use /r here.
  DetailPrint "Удаление временных файлов..."
  ; Leave the extraction directory before deleting it. Windows cannot remove
  ; the process' current directory, which otherwise leaves an empty ns*.tmp
  ; tree behind after every installation.
  SetOutPath "$TEMP"
  Delete "$PLUGINSDIR\focus_browser\setup.exe"
  Delete "$PLUGINSDIR\focus_browser\focus_browser.7z"
  RMDir "$PLUGINSDIR\focus_browser"

  ; Exit immediately after the one explicit error above. Quit avoids NSIS'
  ; additional generic "installation aborted" UI and preserves our exit code,
  ; including in /S mode where MessageBox /SD is never displayed.
  ${If} $InstallFailed == "1"
    SetErrorLevel $WrapperExitCode
    Quit
  ${EndIf}

  ; setup.exe uses several non-zero values for successful outcomes. Normalize
  ; all successful wrapper executions to the conventional process exit code 0.
  SetErrorLevel 0
SectionEnd

; =============================================================================
; Finish Page - Launch Function
; =============================================================================

Function LaunchFocusBrowser
  ${If} $InstallType == "system"
    Exec '"$PROGRAMFILES64\${PRODUCT_COMPANY_PATH}\${PRODUCT_NAME}\Application\chrome.exe"'
  ${Else}
    Exec '"$LOCALAPPDATA\${PRODUCT_COMPANY_PATH}\${PRODUCT_NAME}\Application\chrome.exe"'
  ${EndIf}
FunctionEnd
