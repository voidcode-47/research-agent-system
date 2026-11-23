# 鲸鱼娘桌宠素材出处

- 立绘：`stand.png`（张嘴 Cheerful 正面）/ `blink.png`（闭眼温柔笑）；
  `sad.png` / `sleep.png` / `think.png` / `proud.png` / `run.png` / `wait.png` / `wave.png` / `laugh.png`
  为 AI 动作变体（`image_edit` 基于正面视图生成后抠透明底，统一 420px 高）
- 来源：用户提供的鲸鱼娘三视图参考图（蓝发鲸尾女仆装 Q 版形象），本项目裁取正面视图
- 处理：MediaKit `remove-image-background`（general 场景）抠透明底；`blink.png` 由
  `image_edit` 基于正面视图生成闭眼微笑变体后再抠图；统一裁透明边并缩放至 420px 高
- 叫声：程序合成鲸鸣（WebAudio 低频滑音，无外部音频文件）
- 许可：用户自备参考素材，**仅限个人本地使用**，不得商用分发。
- 动画：`spritesheet.webp`（1536×2288，8 列 11 行，cell 192×208）逐帧精灵表：
  idle 呼吸/鲸尾摆/眨眼 8 帧、wave 8 帧、laugh 合十跳 8 帧、run 提裙游跑 8 帧、doze 趴尾点头 8 帧；
  第 10 行诊断待机＝端白瓷碗吃白饭 8 帧、第 11 行改写待机＝边吃白饭边执笔批注 8 帧（用户指定梗）；
  sad/proud/wait/think 为单帧行 + CSS 辅助动作。帧序列由 `image_edit`（seedream_5.0_pro，会员套餐额度）
  生成、MediaKit 抠图后组装；组装时按连通域面积剔除邻帧越界细碎块、行内统一缩放并保留腾空/步态偏移
- 配音：`voice-wave.wav` 为 TTS 生成台词；其余状态由角色专属合成音动机
  （sine 气泡感滑音短句）发声，仅限个人本地使用