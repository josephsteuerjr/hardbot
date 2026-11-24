!include nsDialogs.nsh
!include WinMessages.nsh
!define MUI_BGCOLOR F4E4CF
!define MUI_TEXTCOLOR 1D1A17
!define MUI_INSTFILESPAGE_COLORS "1D1A17 F4E4CF"

; The wizard closes the installed product before the transaction. Keep a
; bounded check here for concurrent launches and the direct native path.
!macro customCheckAppRunning
  WriteRegStr SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" InstallLocation "$INSTDIR"
  InitPluginsDir
  File /oname=$PLUGINSDIR\hard-process-check.exe "${BUILD_RESOURCES_DIR}\bin\process-check.exe"
  hardCheckAgain:
  nsExec::Exec /TIMEOUT=45000 '"$PLUGINSDIR\hard-process-check.exe" close "$INSTDIR" "${APP_EXECUTABLE_FILENAME}"'
  Pop $R0
  ${If} $R0 != 0
    ${If} ${Silent}
      SetErrorLevel 2
      Quit
    ${Else}
      MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "Не удалось завершить работу ${PRODUCT_NAME}. Подождите несколько секунд и нажмите «Повторить»." IDRETRY hardCheckAgain
      SetErrorLevel 2
      Quit
    ${EndIf}
  ${EndIf}
!macroend

; Keep the native uninstaller in the registry: electron-builder also uses it
; for silent upgrades. Its interactive entry delegates to our own wizard.
!macro customUnInit
  ${If} ${Silent}
    ${If} ${isUpdated}
      GetFullPathName /SHORT $R0 $INSTDIR
      ${If} $R0 != ""
        StrCpy $INSTDIR $R0
      ${EndIf}
    ${EndIf}
  ${Else}
    Exec '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --hard-uninstall'
    Quit
  ${EndIf}
!macroend

!macro customUnInstallCheck
  IfErrors 0 +3
  SetErrorLevel 2
  Quit
  ${If} $R0 != 0
    SetErrorLevel 2
    Quit
  ${EndIf}
!macroend

!ifndef BUILD_UNINSTALLER
Var HardDialog
Var HardFont
Var HardTitleFont
Var HardLabel
!endif

!macro customInstallMode
  StrCpy $isForceCurrentInstall "1"
!macroend

!macro customWelcomePage
  Page custom HardWelcome
Function HardWelcome
  !insertmacro MUI_HEADER_TEXT "${PRODUCT_NAME}" "Рабочее приложение · установка для вашей учётной записи"
  nsDialogs::Create 1018
  Pop $HardDialog
  SetCtlColors $HardDialog 1D1A17 F4E4CF
  CreateFont $HardFont "Segoe UI" 10 400
  CreateFont $HardTitleFont "Segoe UI" 22 600
  ${NSD_CreateLabel} 14u 8u 95% 36u "${PRODUCT_NAME}"
  Pop $HardLabel
  SendMessage $HardLabel ${WM_SETFONT} $HardTitleFont 1
  SetCtlColors $HardLabel 9A4A1E F4E4CF
  ${NSD_CreateLabel} 14u 55u 92% 26u "Ваша работа. В одном окне."
  Pop $HardLabel
  SendMessage $HardLabel ${WM_SETFONT} $HardFont 1
  SetCtlColors $HardLabel 1D1A17 F4E4CF
  ${NSD_CreateLabel} 14u 91u 92% 60u "• Программа установится в AppData без прав администратора.$\r$\n• Ярлыки появятся на рабочем столе и в меню «Пуск».$\r$\n• Обновления проверяются автоматически.$\r$\n• Вход и несохранённая очередь останутся при обновлении."
  Pop $HardLabel
  SendMessage $HardLabel ${WM_SETFONT} $HardFont 1
  SetCtlColors $HardLabel 4A4038 F4E4CF
  ${NSD_CreateLabel} 14u 155u 92% 24u "Нажмите «Далее», чтобы установить ${PRODUCT_NAME}."
  Pop $HardLabel
  SendMessage $HardLabel ${WM_SETFONT} $HardFont 1
  SetCtlColors $HardLabel 9A4A1E F4E4CF
  nsDialogs::Show
FunctionEnd
!macroend

!macro customFinishPage
  Page custom HardFinish
  Function HardFinish
    !insertmacro MUI_HEADER_TEXT "Готово" "${PRODUCT_NAME} установлен для вашей учётной записи"
    nsDialogs::Create 1018
    Pop $HardDialog
    SetCtlColors $HardDialog 1D1A17 F4E4CF
    CreateFont $HardTitleFont "Segoe UI" 22 600
    CreateFont $HardFont "Segoe UI" 10 400
    ${NSD_CreateLabel} 14u 12u 92% 36u "Можно работать."
    Pop $HardLabel
    SendMessage $HardLabel ${WM_SETFONT} $HardTitleFont 1
    SetCtlColors $HardLabel 9A4A1E F4E4CF
    ${NSD_CreateLabel} 14u 63u 92% 70u "Откройте ${PRODUCT_NAME} через ярлык на рабочем столе или в меню «Пуск».$\r$\n$\r$\nПри первом запуске используйте свою ссылку входа или перенесите настройки прежней программы."
    Pop $HardLabel
    SendMessage $HardLabel ${WM_SETFONT} $HardFont 1
    SetCtlColors $HardLabel 4A4038 F4E4CF
    nsDialogs::Show
  FunctionEnd
!macroend

!macro customHeader
  BrandingText "Хард · рабочие приложения"
!macroend
