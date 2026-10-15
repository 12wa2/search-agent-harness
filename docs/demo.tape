# 录制演示 GIF：安装 vhs（https://github.com/charmbracelet/vhs）后运行
#   vhs docs/demo.tape
# 生成 docs/demo.gif（README 用）
Output docs/demo.gif

Set FontSize 15
Set Width 1280
Set Height 800
Set Padding 24
Set TypingSpeed 50ms
Set Theme "Dracula"

Type "search-agent --version"
Enter
Sleep 1s

Type "search-agent demo --top-k 5"
Enter
Sleep 20s
