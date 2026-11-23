# Claude 娘桌宠素材出处

- 立绘：`stand.png`（全身捧书阅读）/ `laugh.png`（全身抱书闭眼灿笑）/ `wave.png`（夹书挥手）/
  `sad.png`（抱书委屈）/ `sleep.png`（枕书坐睡）/ `think.png`（托腮思考）/ `proud.png`（叉腰自信）/
  `run.png`（提裙奔跑）/ `wait.png`（背手等待），均为全身 AI 动作变体
- 来源：角色原设 ZipZipPipe（胖次狐）的 Claude 娘橘白礼服形象；全身母版取自 AI 娘二创档案
  （genderswapai.com/zh-TW/ai-gijinka/claude 与 github.com/lmy414/ai-girl-stickers 收录的同设全身立绘），
  与用户提供的半身参考图同一角色设定
- 处理：MediaKit `remove-image-background`（general 场景）抠透明底；动作变体由
  `image_edit` 基于全身母版生成后再抠图；统一裁透明边并缩放至 420px 高
- 叫声：程序合成低吟（WebAudio 柔和 triangle 滑音，无外部音频文件）
- 许可：同人二创素材，著作权归原作者 ZipZipPipe，**仅限个人本地使用**，不得商用分发。
- 动画：`spritesheet.webp`（1536×2288，8 列 11 行，cell 192×208）逐帧精灵表：
  idle 呼吸/发丝/眨眼 8 帧、wave 8 帧、laugh 抱书跳笑 8 帧、run 提裙跑 8 帧、doze 枕书点头 8 帧；
  第 10 行诊断待机＝捧书逐行核对 8 帧、第 11 行改写待机＝抱书执笔批注 8 帧；
  sad/proud/wait/think 为单帧行 + CSS 辅助动作。帧序列由 `image_edit`（seedream_5.0_pro，会员套餐额度）
  生成、MediaKit 抠图后组装；组装时按连通域面积剔除邻帧越界细碎块、行内统一缩放并保留腾空/步态偏移
- 配音：`voice-sad.wav` / `voice-click.wav` 为 TTS 生成台词；其余状态由角色专属合成音动机
  （triangle 柔和旋律短句）发声，仅限个人本地使用