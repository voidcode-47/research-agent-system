# GPT 娘桌宠素材出处

- 立绘：`stand.png`（全身站立）/ `laugh.png`（全身握拳欢呼灿笑）/ `wave.png`（挥手打招呼）/
  `sad.png`（垂眉委屈）/ `sleep.png`（抱尾坐睡）/ `think.png`（托腮思考）/ `proud.png`（叉腰得意）/
  `run.png`（奔跑）/ `wait.png`（背手等待），均为全身 AI 动作变体
- 来源：角色原设 ZipZipPipe（胖次狐）的 GPT 娘白发龙娘形象；全身母版取自 AI 娘二创档案
  （genderswapai.com/zh-TW/ai-gijinka/gpt 与 github.com/lmy414/ai-girl-stickers 收录的同设全身立绘），
  与用户提供的半身参考图同一角色设定
- 处理：MediaKit `remove-image-background`（general 场景）抠透明底；动作变体由
  `image_edit` 基于全身母版生成后再抠图；统一裁透明边并缩放至 420px 高
- 叫声：程序合成提示音（WebAudio 双音清脆 chime，无外部音频文件）
- 许可：同人二创素材，著作权归原作者 ZipZipPipe，**仅限个人本地使用**，不得商用分发。
- 动画：`spritesheet.webp`（1536×2288，8 列 11 行，cell 192×208）逐帧精灵表：
  idle 呼吸/摆尾/眨眼 8 帧、wave 8 帧、laugh 跳笑 8 帧、run 8 帧、doze 点头 8 帧；
  第 10 行诊断待机＝全息光屏检索 8 帧、第 11 行改写待机＝敲全息键盘 8 帧；
  sad/proud/wait/think 为单帧行 + CSS 辅助动作。帧序列由 `image_edit`（seedream_5.0_pro，会员套餐额度）
  生成、MediaKit 抠图后组装；组装时按连通域面积剔除邻帧越界细碎块、行内统一缩放并保留腾空/步态偏移
- 配音：`voice-click.wav` / `voice-laugh.wav` 为 TTS 生成台词；其余状态由角色专属合成音动机
  （sine 清脆琶音短句）发声，仅限个人本地使用