; FY Orbit · 星轨 — 正式 Windows 安装器（Inno Setup 6 脚本）
;
; 为什么要写这个：原发行室保留只有一个 robocopy 的 Install.bat，它不能
; 在「应用和功能」里显示、不能给用户一个标准的卸载入口、不做数字签名链
; 的挂接。本脚本把发行包交到 Inno Setup，产出带正式卸载程序、开始菜单项、
; 桌面快捷方式、App-Patches 与卸载信息的 setup.exe。
;
; 用法：
;   ISCC deploy/installer/FY-Orbit.iss
; 产物：
;   deploy/installer/output/FY-Orbit-Setup-v<Version>.exe
;
; 前提：先运行 deploy/build_release_package.py --build 得到
;   desktop/sidecar/dist/find-yourself-backend/ 与 web/dist/ 两个构建产物。
;   本脚本只负责把它们装进 Inno Setup 包，不负责编译。

#define MyAppName "FY Orbit · 星轨"
; 版本号可由 ISCC /DMyAppVersion=x.y.z 覆盖（build_installer.ps1 注入 git tag）；
; 未注入时用下面的默认值。
#ifndef MyAppVersion
#define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "FY Orbit"
#define MyAppURL "https://github.com/intp41455/FY-Orbit"
#define MyAppExeName "FY-Orbit.bat"

[Setup]
; 唯一应用标识。每个产品一个 GUID，升级时同一 GUID 才会走「上一版存在」的
; 升级路径，不会装出两条 Start Menu 项。
AppId={{B8CFA8E0-2D6F-4F5E-9A5F-7E4A1B2C3D4E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
DefaultDirName={localappdata}\FYOrbit
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=FY-Orbit-Setup-v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
WizardStyle=modern
UninstallDisplayIcon={app}\web\dist\favicon.ico
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 本地 Web 桌面发行版
VersionInfoProductName={#MyAppName}
; 未配置代码签名证书时 Inno Setup 不会自动签名 setup.exe；
; 接入证书后在这里加：
;   SignTool=signtool
;   SignedUninstaller=yes

[Languages]
; 用英文语言包即可；不同 Inno Setup 版本的中文语言文件路径/名字不一致，
; 且缺失该 .isl 会让 ISCC 直接报错。要中文界面时再在安装后自行挂上。
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "额外任务："

[Files]
; 发行包内容来自同一构建链组装的 FY-Orbit 目录（build_release_package.py）。
; 发行时 ISCC 的工作目录必须是仓库根，这样 Source 路径能对上。
Source: "..\..\.build_stage\FY-Orbit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\web\dist\favicon.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\web\dist\favicon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\web"
Type: filesandordirs; Name: "{app}\find-yourself-backend"
; 用户数据目录 data\\ 默认保留，由用户自行决定删除时机（保护数据）。
