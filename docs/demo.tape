# 录制演示 GIF：安装 vhs（https://github.com/charmbracelet/vhs）后运行
#   vhs docs/demo.tape
# 生成 docs/demo.gif（README 首屏用）
#
# 没有 vhs 时：scoop install vhs（Windows）/ brew install vhs（macOS），
# 或直接手动执行下面两条命令并用任意录屏工具录制。
Output docs/demo.gif

Set FontSize 15
Set Width 1280
Set Height 800
Set Padding 24
Set TypingSpeed 50ms
Set Theme "Dracula"
Set Shell "powershell"

Type "search-agent --version"
Enter
Sleep 1s

# ① 离线演示：8 阶段时间线 + 交付列表 + 运行指标（脚本化 LLM，零 API key）
Type 'search-agent demo "找有 3 年以上经验、做过高并发系统的后端工程师，最好来自大厂" --top-k 5'
Enter
Sleep 20s

# ② 消融表：同一语料逐行只关掉一个机制（"每个机制值多少钱"）
Type "search-agent eval --generated 120 --seed 7 --ablate --markdown"
Enter
Sleep 20s
