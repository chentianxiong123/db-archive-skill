@echo off
chcp 65001 >nul
echo ================================================
echo  微信备份解密  (HiSuite 14.0.0.320 补丁版)
echo ================================================
echo.
cd /d C:\Users\a1\Desktop\wechat-backup-decrypt\tools
if not exist kobackupdec.py (
  echo [错误] 找不到 kobackupdec.py
  pause & exit /b 1
)
if exist "C:\Users\a1\Documents\HiSuite\decrypted" (
  echo [提示] 输出目录已存在，kobackupdec 不允许覆盖。
  echo        请先删除或改名 C:\Users\a1\Documents\HiSuite\decrypted
  pause & exit /b 1
)
echo [开始] 密码: aA123456
echo.
python kobackupdec.py aA123456 "C:\Users\a1\Documents\HiSuite\backup\HUAWEI nova youth_2026-10-26 21.47.14" "C:\Users\a1\Documents\HiSuite\decrypted" -e -w
echo.
echo ================================================
echo  跑完了。判断标准：
echo    Empty CheckMsg!              -^> 补丁没生效
echo    Decryptor checks failed      -^> 密码错，改这个 .bat 里的密码
echo    写出 .db 文件               -^> 成功
echo ================================================
pause
