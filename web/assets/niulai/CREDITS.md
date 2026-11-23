# 牛来桌宠素材出处

- 立绘：`stand.png` / `shout.png` / `blink.png`（"原皮"橙色牛形象，与用户指定参考图一致）；
  `sad.png` / `sleep.png` / `think.png` / `proud.png` / `run.png` / `wait.png` / `wave.png` / `laugh.png`
  为 AI 动作变体（`image_edit` 基于原皮站姿生成后抠透明底，统一 420px 高）
- 叫声：`mama-1.mp3` / `mama-2.mp3`（"妈~~妈~~"原声降噪）、`reply.mp3`（"牛来！"）
- 来源：whitefirer/dsh-niulai-pet（MIT License）
  - 仓库：https://github.com/whitefirer/dsh-niulai-pet
  - 在线试玩：https://whitefirer.org/niulai-pet/
  - 本项目素材提取自官方试玩页独立脚本 `niulai-standalone.js` 内嵌的"牛来·原皮"皮肤资源
- 形象原型：电影《牛来》抽象黄牛 meme（粉丝二创抠图精修版）
- 许可：仓库整体 MIT；电影形象本身为第三方 meme，**仅限个人本地使用**，不得商用分发。
- 动画：`spritesheet.webp`（1536×2288，8 列 11 行，cell 192×208）逐帧精灵表：
  idle 呼吸/抖耳/摆尾/眨眼 8 帧、wave 举蹄 8 帧、laugh 四蹄跳 8 帧、run 小跑 8 帧、doze 蹲睡点头 8 帧；
  第 10 行诊断待机＝蹄捧论文翻页 8 帧、第 11 行改写待机＝趴桌蹄夹笔狂写 8 帧；
  sad/proud/wait/think 为单帧行 + CSS 辅助动作。帧序列由 `image_edit`（seedream_5.0_pro，会员套餐额度）
  生成、MediaKit 抠图后组装；组装时按连通域面积剔除邻帧越界细碎块、行内统一缩放并保留腾空/步态偏移
- 配音：笑保留"妈~~"原声；`voice-wave.wav` 为 TTS 生成打招呼台词；其余状态由角色专属合成音动机
  （sawtooth 憨厚低音短句）发声，仅限个人本地使用