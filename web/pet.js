/* =========================================================
 * 工作台桌宠（Multi-Pet）
 * 角色注册表 PETS：每个角色自带素材几何、动作映射、台词、叫声。
 * 两种渲染模式：
 *   sheet —— 精灵表逐帧（奶蛙 / 哈基米）
 *   pose  —— 单立绘 + CSS 动作 + 表情立绘切换（牛来 / 奶龙 / 蓝鲸）
 * 素材来源与许可见 web/assets 下各角色目录的 CREDITS.md（个人本地使用）
 * ========================================================= */
"use strict";

(function () {
  const SCALE = 0.75;
  const LS_POS = "naiwaPet.pos";
  const LS_SOUND = "naiwaPet.sound";
  const LS_HIDDEN = "naiwaPet.hidden";
  const LS_PET = "naiwaPet.pet";
  const LS_SCALE = "naiwaPet.scale";
  const DOZE_MS = 45000;               // 无操作多久后打盹
  const UNCONFIRMED_MS = 12000;        // 点击后任务未真正建立的看门狗上限
  const FOLLOW_MS = 900;               // 鼠标跟随眼神保持时长
  const DASH_COOLDOWN = 8000;          // 滚动跑动冷却
  const POUNCE_COOLDOWN = 2600;        // 哈基米扑鼠标冷却

  // ---------- 角色注册表 ----------
  const PETS = {
    naiwa: {
      label: "奶蛙", tag: "呱呱陪写论文", mode: "sheet", greet: "我来陪你写论文~",
      sheet: "assets/naiwa/spritesheet.webp?v=notail2", cols: 8, rows: 11, cellW: 192, cellH: 208,
      states: {
        idle:    { row: 0, frames: 7, fps: 5,  loop: true },
        search:  { row: 1, frames: 8, fps: 7.5,  loop: true },
        wait:    { row: 6, frames: 6, fps: 5,  loop: true },
        think:   { row: 7, frames: 6, fps: 4,  loop: true },
        wave:    { row: 3, frames: 4, fps: 3.5, loops: 2 },
        laugh:   { row: 4, frames: 5, fps: 5, loops: 4 },
        sad:     { row: 5, frames: 8, fps: 3.5, loops: 1 },
        proud:   { row: 8, frames: 6, fps: 5,  loops: 2 },
        run:     { row: 1, frames: 8, fps: 8, loops: 1 },
        runleft: { row: 2, frames: 8, fps: 8, loops: 1 },
        doze:    { row: 0, frames: 7, fps: 1.2, loop: true },
        dangle:  { row: 6, frames: 6, fps: 8, loop: true },
        hop:     { row: 3, frames: 4, fps: 3.5, loops: 2, act: "hop" },
        spin:    { row: 1, frames: 8, fps: 6.5,  loops: 1, act: "spin" },
        bow:     { row: 0, frames: 7, fps: 3, loops: 1, act: "bow" },
        peek:    { row: 2, frames: 8, fps: 5,  loops: 1, act: "peek" },
        stretch: { row: 0, frames: 7, fps: 2, loops: 1, act: "stretch" },
      },
      look: { row: 9, frames: 16 },
      poses: {
        diagnose: "assets/naiwa/pose-diagnose.png?v=notail2",
        rewrite: "assets/naiwa/pose-rewrite.png",
      },
      task: { upload: "wait", diagnose: "diagnose", similar: "search", rewrite: "rewrite", batch: "rewrite", ask: "think", export: "wait", backup: "wait", format: "think", guideline: "think", research: "search" },
      bubbles: {
        diagnose: "放大镜就位，仔细检查…", rewrite: "趴好，开写！", laugh: "哈哈哈哈哈哈！",
        wave: "搞定！", sad: "呜…出错了", wait: "稍等稍等…", search: "跑去查资料~",
        think: "让我想想…", proud: "哼，我厉害吧~", doze: "Zzz…", dangle: "哇啊—放我下来~",
        hop: "呱！跳一个~", bow: "呱~ 有礼了", peek: "左看看右看看~",
      },
      clickLines: ["戳我干嘛~", "在呢在呢", "好好写论文！", "今天也要加油鸭"],
      clickReactions: ["wave", "wave", "proud", "think", "hop", "bow"],
      voice: { samples: ["assets/naiwa/laugh.m4a"], playS: 2.6, timbre: "triangle",
        motifs: { wave: [659, 880], think: [880, 988], wait: [659, 659], click: [784, 1046], proud: [784, 988, 1175], dance: [1046, 880, 1046, 1319], doze: [392, 262], sad: [659, 523, 392] } },
      reactionMs: { wave: 2300, laugh: 4000, sad: 2300, proud: 2400, run: 1000, runleft: 1000, hop: 2300, spin: 1250, bow: 2350, peek: 1600, stretch: 3500 },
      idleActs: ["hop", "bow", "peek"],
    },
    niulai: {
      label: "牛来", tag: "妈~~妈~~ 抽象黄牛", mode: "sheet", greet: "牛来！陪你写论文",
      sheet: "assets/niulai/spritesheet.webp", cols: 8, rows: 11, cellW: 192, cellH: 208,
      states: {
        idle:    { row: 0, frames: 8, fps: 5,  loop: true },
        wave:    { row: 1, frames: 8, fps: 5.5,  loops: 2 },
        laugh:   { row: 2, frames: 8, fps: 6.5,  loops: 2 },
        run:     { row: 3, frames: 8, fps: 8, loops: 1 },
        runleft: { row: 3, frames: 8, fps: 8, loops: 1 },
        search:  { row: 3, frames: 8, fps: 8, loop: true },
        doze:    { row: 4, frames: 8, fps: 1.5,  loop: true },
        sad:     { row: 5, frames: 1, fps: 1,  loop: true },
        proud:   { row: 6, frames: 1, fps: 1,  loop: true },
        wait:    { row: 0, frames: 8, fps: 3.5,  loop: true },
        think:   { row: 0, frames: 8, fps: 3.5, loop: true },
        diagnose:{ row: 9, frames: 8, fps: 3.5, loop: true },
        rewrite: { row: 10, frames: 8, fps: 5, loop: true },
        dangle:  { row: 1, frames: 8, fps: 7,  loop: true },
        hop:     { row: 1, frames: 8, fps: 5,  loops: 2, act: "hop" },
        spin:    { row: 3, frames: 8, fps: 6.5,  loops: 1, act: "spin" },
        bow:     { row: 0, frames: 8, fps: 3,  loops: 1, act: "bow" },
        peek:    { row: 3, frames: 8, fps: 5,  loops: 1, act: "peek" },
        stretch: { row: 4, frames: 8, fps: 2.5,  loops: 1, act: "stretch" },
      },
      task: { upload: "wait", diagnose: "diagnose", similar: "search", rewrite: "rewrite", batch: "rewrite", ask: "think", export: "wait", backup: "wait", format: "think", guideline: "think", research: "search" },
      bubbles: {
        diagnose: "交给我你就操心吧", rewrite: "妈——！开写", laugh: "妈~~妈~~",
        wave: "牛来！", sad: "呜…牛不对", wait: "稍等稍等…", search: "跑去查资料~",
        think: "交给我你就操心吧", proud: "哼，我厉害吧~", doze: "Zzz…",
        dangle: "哇啊—放我下来~", dance: "牛来转圈圈~", run: "哒哒哒，跑两步~",
        spin: "妈~~ 转圈圈!", hop: "牛来跳!", stretch: "伸个懒腰…妈~",
      },
      clickLines: ["妈——！", "牛来！", "交给我你就操心吧", "今天也要加油鸭"],
      clickReactions: ["wave", "wave", "proud", "think", "dance", "run", "spin", "hop"],
      voice: { samples: ["assets/niulai/mama-1.mp3", "assets/niulai/mama-2.mp3"], reply: "assets/niulai/reply.mp3", playS: 3.4, timbre: "sawtooth",
        clips: [{ s: "wave", u: "assets/niulai/voice-wave.wav" }],
        motifs: { wave: [196, 262, 392], think: [196, 220], wait: [196, 196], click: [392, 523], proud: [262, 330, 392], dance: [392, 330, 392, 523], doze: [147, 98], sad: [262, 196, 147] } },
      reactionMs: { wave: 2950, laugh: 2500, sad: 1600, proud: 1600, fly: 1300, run: 1000, runleft: 1000, hop: 3200, spin: 1250, bow: 2700, peek: 1600, stretch: 3200 },
      idleActs: ["spin", "hop", "stretch"],
    },
    whalegirl: {
      label: "鲸鱼娘", tag: "深海女仆·咕嘟咕嘟", mode: "sheet", greet: "主人，我来陪你写论文~",
      sheet: "assets/whalegirl/spritesheet.webp", cols: 8, rows: 11, cellW: 192, cellH: 208,
      states: {
        idle:    { row: 0, frames: 8, fps: 5,  loop: true },
        wave:    { row: 1, frames: 8, fps: 5.5,  loops: 2 },
        laugh:   { row: 2, frames: 8, fps: 6.5,  loops: 2 },
        run:     { row: 3, frames: 8, fps: 8, loops: 1 },
        runleft: { row: 3, frames: 8, fps: 8, loops: 1 },
        search:  { row: 3, frames: 8, fps: 8, loop: true },
        doze:    { row: 4, frames: 8, fps: 1.5,  loop: true },
        sad:     { row: 5, frames: 1, fps: 1,  loop: true },
        proud:   { row: 6, frames: 1, fps: 1,  loop: true },
        wait:    { row: 0, frames: 8, fps: 3.5,  loop: true },
        think:   { row: 0, frames: 8, fps: 3.5, loop: true },
        diagnose:{ row: 9, frames: 8, fps: 3.5, loop: true },
        rewrite: { row: 10, frames: 8, fps: 5, loop: true },
        dangle:  { row: 1, frames: 8, fps: 7,  loop: true },
        hop:     { row: 1, frames: 8, fps: 5,  loops: 2, act: "hop" },
        spin:    { row: 3, frames: 8, fps: 6.5,  loops: 1, act: "spin" },
        bow:     { row: 0, frames: 8, fps: 3,  loops: 1, act: "bow" },
        peek:    { row: 3, frames: 8, fps: 5,  loops: 1, act: "peek" },
        stretch: { row: 4, frames: 8, fps: 2.5,  loops: 1, act: "stretch" },
      },
      task: { upload: "wait", diagnose: "diagnose", similar: "search", rewrite: "rewrite", batch: "rewrite", ask: "think", export: "wait", backup: "wait", format: "think", guideline: "think", research: "search" },
      bubbles: {
        diagnose: "声呐检查中…主人稍等", rewrite: "女仆式改稿中…", laugh: "噗——！",
        wave: "主人，搞定啦~", sad: "呜…沉底了", wait: "稍等稍等…", search: "游去查资料~",
        think: "深海里没有 deadline", proud: "噗——！厉害吧", doze: "Zzz…咕嘟", dangle: "哇啊—放我回海里~",
        dance: "主人看我转圈圈~", run: "游快一点~哒哒哒",
        bow: "主人，有礼了~", hop: "咕嘟！跳一下~", stretch: "伸个懒腰…咕嘟",
      },
      clickLines: ["主人，戳我干嘛~", "咕嘟咕嘟", "深海里没有 deadline", "今天也要加油哦，主人"],
      clickReactions: ["wave", "laugh", "proud", "think", "dance", "run", "bow", "hop"],
      voice: { synth: "whale", clips: [{ s: "wave", u: "assets/whalegirl/voice-wave.wav" }], timbre: "sine",
        motifs: { wave: [392, 523, 784], think: [523, 587], wait: [392, 392], click: [784, 1046], proud: [523, 659, 784], dance: [784, 659, 784, 1046], doze: [196, 147], sad: [330, 247, 196], laugh: [392, 523, 659, 784] } },
      reactionMs: { wave: 2950, laugh: 2500, sad: 1600, proud: 1600, run: 1000, runleft: 1000, hop: 3200, spin: 1250, bow: 2700, peek: 1600, stretch: 3200 },
      idleActs: ["bow", "hop", "stretch"],
    },
    gptgirl: {
      label: "GPT 娘", tag: "白发龙角·秒答少女", mode: "sheet", greet: "你好呀，我来陪你写论文~",
      sheet: "assets/gptgirl/spritesheet.webp", cols: 8, rows: 11, cellW: 192, cellH: 208,
      states: {
        idle:    { row: 0, frames: 8, fps: 5,  loop: true },
        wave:    { row: 1, frames: 8, fps: 5.5,  loops: 2 },
        laugh:   { row: 2, frames: 8, fps: 6.5,  loops: 2 },
        run:     { row: 3, frames: 8, fps: 8, loops: 1 },
        runleft: { row: 3, frames: 8, fps: 8, loops: 1 },
        search:  { row: 3, frames: 8, fps: 8, loop: true },
        doze:    { row: 4, frames: 8, fps: 1.5,  loop: true },
        sad:     { row: 5, frames: 1, fps: 1,  loop: true },
        proud:   { row: 6, frames: 1, fps: 1,  loop: true },
        wait:    { row: 0, frames: 8, fps: 3.5,  loop: true },
        think:   { row: 0, frames: 8, fps: 3.5, loop: true },
        diagnose:{ row: 9, frames: 8, fps: 3.5, loop: true },
        rewrite: { row: 10, frames: 8, fps: 5, loop: true },
        dangle:  { row: 1, frames: 8, fps: 7,  loop: true },
        hop:     { row: 1, frames: 8, fps: 5,  loops: 2, act: "hop" },
        spin:    { row: 3, frames: 8, fps: 6.5,  loops: 1, act: "spin" },
        bow:     { row: 0, frames: 8, fps: 3,  loops: 1, act: "bow" },
        peek:    { row: 3, frames: 8, fps: 5,  loops: 1, act: "peek" },
        stretch: { row: 4, frames: 8, fps: 2.5,  loops: 1, act: "stretch" },
      },
      task: { upload: "wait", diagnose: "diagnose", similar: "search", rewrite: "rewrite", batch: "rewrite", ask: "think", export: "wait", backup: "wait", format: "think", guideline: "think", research: "search" },
      bubbles: {
        diagnose: "让我检索一下…", rewrite: "生成改稿中…", laugh: "嘿嘿，答对啦！",
        wave: "回答生成完毕~", sad: "呜…推理出错", wait: "稍等，思考中…", search: "跑去查资料~",
        think: "让我想想…", proud: "哼，我的答案最棒~", doze: "Zzz…", dangle: "哇啊—放我下来~",
        dance: "转圈圈庆祝一下~", run: "哒哒哒，检索去~",
        spin: "转圈庆祝，秒答!", hop: "跳一下，答案更快~", bow: "你好呀~ 鞠躬",
      },
      clickLines: ["要我再续写一段吗？", "答案生成中…", "让我想想…", "今天也要加油鸭"],
      clickReactions: ["wave", "laugh", "proud", "think", "dance", "run", "spin", "hop"],
      voice: { synth: "chime", clips: [{ s: "click", u: "assets/gptgirl/voice-click.wav" }, { s: "laugh", u: "assets/gptgirl/voice-laugh.wav" }], timbre: "sine",
        motifs: { wave: [1046, 1568, 2093], think: [1318, 1568], wait: [1046, 1046], click: [2093, 2637], proud: [1568, 2093, 2637], dance: [2093, 1760, 2093, 2637], doze: [523, 392], sad: [880, 660, 523], laugh: [1568, 2093, 2637, 2093] } },
      reactionMs: { wave: 2950, laugh: 2500, sad: 1600, proud: 1600, run: 1000, runleft: 1000, hop: 3200, spin: 1250, bow: 2700, peek: 1600, stretch: 3200 },
      idleActs: ["hop", "spin", "bow"],
    },
    claudegirl: {
      label: "Claude 娘", tag: "橙发捧书·慎思少女", mode: "sheet", greet: "我会认真陪你写论文的~",
      sheet: "assets/claudegirl/spritesheet.webp", cols: 8, rows: 11, cellW: 192, cellH: 208,
      states: {
        idle:    { row: 0, frames: 8, fps: 5,  loop: true },
        wave:    { row: 1, frames: 8, fps: 5.5,  loops: 2 },
        laugh:   { row: 2, frames: 8, fps: 6.5,  loops: 2 },
        run:     { row: 3, frames: 8, fps: 8, loops: 1 },
        runleft: { row: 3, frames: 8, fps: 8, loops: 1 },
        search:  { row: 3, frames: 8, fps: 8, loop: true },
        doze:    { row: 4, frames: 8, fps: 1.5,  loop: true },
        sad:     { row: 5, frames: 1, fps: 1,  loop: true },
        proud:   { row: 6, frames: 1, fps: 1,  loop: true },
        wait:    { row: 0, frames: 8, fps: 3.5,  loop: true },
        think:   { row: 0, frames: 8, fps: 3.5, loop: true },
        diagnose:{ row: 9, frames: 8, fps: 3.5, loop: true },
        rewrite: { row: 10, frames: 8, fps: 5, loop: true },
        dangle:  { row: 1, frames: 8, fps: 7,  loop: true },
        hop:     { row: 1, frames: 8, fps: 5,  loops: 2, act: "hop" },
        spin:    { row: 3, frames: 8, fps: 6.5,  loops: 1, act: "spin" },
        bow:     { row: 0, frames: 8, fps: 3,  loops: 1, act: "bow" },
        peek:    { row: 3, frames: 8, fps: 5,  loops: 1, act: "peek" },
        stretch: { row: 4, frames: 8, fps: 2.5,  loops: 1, act: "stretch" },
      },
      task: { upload: "wait", diagnose: "diagnose", similar: "search", rewrite: "rewrite", batch: "rewrite", ask: "think", export: "wait", backup: "wait", format: "think", guideline: "think", research: "search" },
      bubbles: {
        diagnose: "仔细核对每一处…", rewrite: "捧书改稿中…", laugh: "嘿嘿，想明白啦！",
        wave: "这次想清楚了~", sad: "呜…我再想想", wait: "稍等，我在思考…", search: "跑去查资料~",
        think: "嗯…让我再想深一点", proud: "充分考虑过的答案~", doze: "Zzz…", dangle: "哇啊—放我下来~",
        dance: "唔…转晕了啦", run: "小跑一下，思路更清楚",
        bow: "嗯…向你致意", stretch: "伸个懒腰，再想深一点", peek: "探头看看进度~",
      },
      clickLines: ["嗯…让我再想一下", "这本书借你看看？", "我会认真陪你的", "慢一点，想清楚再写"],
      clickReactions: ["wave", "laugh", "proud", "think", "dance", "run", "bow", "stretch"],
      voice: { synth: "hum", clips: [{ s: "sad", u: "assets/claudegirl/voice-sad.wav" }, { s: "click", u: "assets/claudegirl/voice-click.wav" }], timbre: "triangle",
        motifs: { wave: [330, 440, 523], think: [440, 494], wait: [330, 330], click: [523, 659], proud: [440, 523, 659], dance: [523, 440, 523, 659], doze: [262, 196], sad: [392, 330, 262], laugh: [440, 523, 659, 523] } },
      reactionMs: { wave: 2950, laugh: 2500, sad: 1600, proud: 1600, run: 1000, runleft: 1000, hop: 3200, spin: 1250, bow: 2700, peek: 1600, stretch: 3200 },
      idleActs: ["bow", "stretch", "peek"],
    },
    geminigirl: {
      label: "Gemini 娘", tag: "蓝紫猫耳·星芒双子", greet: "Gemini 娘上线~ 双屏检索陪你写论文！", mode: "sheet",
      sheet: "assets/geminigirl/spritesheet.webp", cols: 8, rows: 11, cellW: 192, cellH: 208,
      states: {
        idle:    { row: 0, frames: 8, fps: 5,  loop: true },
        wave:    { row: 1, frames: 8, fps: 5.5,  loops: 2 },
        laugh:   { row: 2, frames: 8, fps: 6.5,  loops: 2 },
        run:     { row: 3, frames: 8, fps: 8, loops: 1 },
        runleft: { row: 3, frames: 8, fps: 8, loops: 1 },
        search:  { row: 3, frames: 8, fps: 8, loop: true },
        doze:    { row: 4, frames: 8, fps: 1.5,  loop: true },
        sad:     { row: 5, frames: 1, fps: 1,  loop: true },
        proud:   { row: 6, frames: 1, fps: 1,  loop: true },
        wait:    { row: 0, frames: 8, fps: 3.5,  loop: true },
        think:   { row: 0, frames: 8, fps: 3.5, loop: true },
        diagnose:{ row: 9, frames: 8, fps: 3.5, loop: true },
        rewrite: { row: 10, frames: 8, fps: 5, loop: true },
        dangle:  { row: 1, frames: 8, fps: 7,  loop: true },
        hop:     { row: 1, frames: 8, fps: 5,  loops: 2, act: "hop" },
        spin:    { row: 3, frames: 8, fps: 6.5,  loops: 1, act: "spin" },
        bow:     { row: 0, frames: 8, fps: 3,  loops: 1, act: "bow" },
        peek:    { row: 3, frames: 8, fps: 5,  loops: 1, act: "peek" },
        stretch: { row: 4, frames: 8, fps: 2.5,  loops: 1, act: "stretch" },
      },
      task: { upload: "wave", diagnose: "diagnose", research: "search", similar: "search", guideline: "think", rewrite: "rewrite", batch: "rewrite", ask: "think", export: "wave", backup: "wave", format: "think" },
      bubbles: {
        diagnose: "双屏对照检索中…", rewrite: "双手星笔改稿中…", laugh: "星芒一闪，搞定啦！",
        wave: "双屏结果都给你~", sad: "呜…星芒暗了", wait: "稍等，双子检索中…", search: "跑去查资料~",
        think: "嗯…两种思路都比一比", proud: "星芒认证的答案~", doze: "Zzz…", dangle: "哇啊—放我下来~",
        dance: "星芒转晕了啦", run: "小跑一下，灵感更快",
        spin: "星芒转圈~", hop: "双子跳跳!", bow: "喵~ 有礼了",
      },
      clickLines: ["双子模式，启动~", "星芒给你指路！", "两种答案都帮你算好啦", "喵？戳我干嘛~"],
      clickReactions: ["wave", "laugh", "proud", "think", "dance", "run", "spin", "hop"],
      voice: { synth: "bell", clips: [{ s: "wave", u: "assets/geminigirl/voice-wave.wav" }, { s: "click", u: "assets/geminigirl/voice-click.wav" }], timbre: "sine",
        motifs: { wave: [[523, 659], [784, 988], [1046, 1318]], think: [[659, 880], [784, 988]], wait: [[523, 659], [523, 659]], click: [[880, 1175], [1046, 1318]], proud: [[659, 880], [880, 1175], [1046, 1318]], dance: [[784, 988], [659, 880], [784, 988], [1046, 1318]], doze: [[392, 523], [262, 330]], sad: [[659, 784], [523, 659], [392, 494]], laugh: [[784, 988], [1046, 1318], [1318, 1568], [1046, 1318]] } },
      reactionMs: { wave: 2950, laugh: 2500, sad: 1600, proud: 1600, run: 1000, runleft: 1000, hop: 3200, spin: 1250, bow: 2700, peek: 1600, stretch: 3200 },
      idleActs: ["hop", "spin", "bow"],
    },
    grokgirl: {
      label: "Grok 娘", tag: "金发哥特·扛斧吐槽", greet: "Grok 娘驾到~ 真话时间，陪你写论文！", mode: "sheet",
      sheet: "assets/grokgirl/spritesheet.webp", cols: 8, rows: 11, cellW: 192, cellH: 208,
      states: {
        idle:    { row: 0, frames: 8, fps: 5,  loop: true },
        wave:    { row: 1, frames: 8, fps: 5.5,  loops: 2 },
        laugh:   { row: 2, frames: 8, fps: 6.5,  loops: 2 },
        run:     { row: 3, frames: 8, fps: 8, loops: 1 },
        runleft: { row: 3, frames: 8, fps: 8, loops: 1 },
        search:  { row: 3, frames: 8, fps: 8, loop: true },
        doze:    { row: 4, frames: 8, fps: 1.5,  loop: true },
        sad:     { row: 5, frames: 1, fps: 1,  loop: true },
        proud:   { row: 6, frames: 1, fps: 1,  loop: true },
        wait:    { row: 0, frames: 8, fps: 3.5,  loop: true },
        think:   { row: 0, frames: 8, fps: 3.5, loop: true },
        diagnose:{ row: 9, frames: 8, fps: 3.5, loop: true },
        rewrite: { row: 10, frames: 8, fps: 5, loop: true },
        dangle:  { row: 1, frames: 8, fps: 7,  loop: true },
        hop:     { row: 1, frames: 8, fps: 5,  loops: 2, act: "hop" },
        spin:    { row: 3, frames: 8, fps: 6.5,  loops: 1, act: "spin" },
        bow:     { row: 0, frames: 8, fps: 3,  loops: 1, act: "bow" },
        peek:    { row: 3, frames: 8, fps: 5,  loops: 1, act: "peek" },
        stretch: { row: 4, frames: 8, fps: 2.5,  loops: 1, act: "stretch" },
      },
      task: { upload: "wave", diagnose: "diagnose", research: "search", similar: "search", guideline: "think", rewrite: "rewrite", batch: "rewrite", ask: "think", export: "wave", backup: "wave", format: "think" },
      bubbles: {
        diagnose: "刷着吐槽流帮你查…", rewrite: "倚斧狂写批注中…", laugh: "哈！这题也配难我？",
        wave: "真话时间到~", sad: "啧…失误了，再来", wait: "等着，姐在查…", search: "扛斧跑去查资料~",
        think: "嗯…让姐想想怎么吐槽", proud: "斧头认证的答案~", doze: "Zzz…", dangle: "喂—放姐下来！",
        dance: "斧头都转晕了", run: "扛斧小跑一下",
        stretch: "姐伸个懒腰…", spin: "斧头转圈，吐槽继续", hop: "跳一下，真话时间",
      },
      clickLines: ["真话时间，问吧", "姐的斧头只砍废话", "戳我？胆子不小嘛~", "这论文姐罩了"],
      clickReactions: ["wave", "laugh", "proud", "think", "dance", "run", "stretch", "spin"],
      voice: { synth: "slide", clips: [{ s: "wave", u: "assets/grokgirl/voice-wave.wav" }, { s: "click", u: "assets/grokgirl/voice-click.wav" }], timbre: "sawtooth",
        motifs: { wave: [440, 392, 330], think: [330, 294], wait: [262, 262], click: [392, 330], proud: [330, 392, 440], dance: [440, 392, 330, 262], doze: [196, 147], sad: [330, 262, 196], laugh: [440, 523, 440, 330] } },
      reactionMs: { wave: 2950, laugh: 2500, sad: 1600, proud: 1600, run: 1000, runleft: 1000, hop: 3200, spin: 1250, bow: 2700, peek: 1600, stretch: 3200 },
      idleActs: ["stretch", "spin", "hop"],
    },
  };
  const PET_IDS = Object.keys(PETS);

  // 按钮级即时联动：点击瞬间就反应，不等接口返回
  const BTN_TASK = {
    "btn-paper-upload": "upload",
    "btn-paper-diagnose": "diagnose",
    "btn-paper-similar": "similar",
    "btn-paper-similar-refresh": "similar",
    "btn-paper-batch": "batch",
    "btn-paper-guide": "guideline",
    "btn-paper-rewrite": "rewrite",
  };
  const BTN_CANCEL = {
    "btn-paper-upload-cancel": "upload",
    "btn-paper-cancel": "diagnose",
    "btn-paper-similar-cancel": "similar",
    "btn-paper-batch-cancel": "batch",
    "btn-paper-guide-cancel": "guideline",
    "btn-paper-rewrite-cancel": "rewrite",
  };

  // 耗时操作注册表：待机气泡 / 完成动作 / 完成音阶 / 失败文案
  const OPS = {
    upload:    { wait: "解析论文中…", done: "论文解析完成！", fail: "解析失败了，再传一次嘛", notes: [523, 659, 784], react: "wave" },
    diagnose:  { wait: "四维诊断中…", done: "诊断完成啦！", fail: "诊断出错了，再试一次嘛", notes: [659, 784, 988], react: "proud" },
    research:  { wait: "调研参考文献中…", done: "文献调研完成！", fail: "调研失败了，稍后再试嘛", notes: [784, 988, 1175], react: "wave" },
    similar:   { wait: "检索相似论文中…", done: "相似论文找齐啦！", fail: "检索失败了，再试一次嘛", notes: [784, 988, 1175], react: "wave" },
    guideline: { wait: "生成改写指南中…", done: "改写指南写好啦！", fail: "指南生成失败，再试一次嘛", notes: [659, 880, 1046], react: "proud" },
    rewrite:   { wait: "逐段改写中…", done: "改写完成啦！", fail: "改写出错了，再改一次嘛", notes: [880, 1046, 1318], react: "laugh" },
    batch:     { wait: "批量改写中…", done: "批量改写完成！", fail: "批量改写中断了，看看进度嘛", notes: [880, 1046, 1318], react: "laugh" },
    ask:       { wait: "思考回答中…", done: "回答写好啦！", fail: "回答失败了，再问一次嘛", notes: [659, 880, 1046], react: "proud" },
    export:    { wait: "导出文档中…", done: "导出完成，已下载！", fail: "导出失败了，再试一次嘛", notes: [1046, 1318, 1568, 2093], react: "proud" },
    backup:    { wait: "打包备份中…", done: "备份已下载！", fail: "备份失败了，再试一次嘛", notes: [523, 659, 880], react: "wave" },
    format:    { wait: "格式检查中…", done: "格式检查完成！", fail: "格式检查失败，再试一次嘛", notes: [659, 784, 988], react: "proud" },
  };
  function chimeNotes(notes) {
    if (!soundOn || !audioUnlocked) return;
    try {
      ensureCtx();
      notes.forEach((f, i) => setTimeout(() => tone(f, f * 1.01, 0.16, "sine", 0.06), i * 110));
    } catch (_) { /* 音频不可用时静默 */ }
  }
  function opStart(key) {
    beginTask(key, true);
    const op = OPS[key];
    if (op) showBubble(op.wait, 3000);
  }
  function opEnd(key, ok) {
    activeTasks.delete(key);
    lastActivity = performance.now();
    const op = OPS[key];
    if (!op) { react(ok ? "wave" : "sad"); return; }
    if (ok) { chimeNotes(op.notes); react(op.react); showBubble(op.done, 2600); }
    else { react("sad"); showBubble(op.fail, 2600); }
  }

  const reducedMotion = window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let petId = localStorage.getItem(LS_PET);
  if (!PETS[petId]) petId = "naiwa";
  let pet = PETS[petId];

  // ---------- DOM ----------
  const root = document.createElement("div");
  root.id = "naiwa-pet";
  root.innerHTML =
    '<div class="naiwa-bubble" role="status"></div>' +
    '<div class="naiwa-stage">' +
    '  <div class="naiwa-sprite"></div>' +
    '</div>';
  const badge = document.createElement("button");
  badge.id = "naiwa-pet-badge";
  badge.type = "button";
  badge.title = "唤出桌宠";
  document.body.appendChild(root);
  document.body.appendChild(badge);

  const stage = root.querySelector(".naiwa-stage");
  const spriteEl = root.querySelector(".naiwa-sprite");
  const bubbleEl = root.querySelector(".naiwa-bubble");

  // ---------- 状态 ----------
  const activeTasks = new Map();   // kind -> {state, confirmed, at}，后插入的优先
  let reaction = null;             // {name, until}
  let glance = null;               // {frames:[...], start} 待机左右瞥
  let follow = null;               // {frame, until} 鼠标跟随眼神
  let dragInfo = null;
  let lastVisual = "";
  let animStart = 0;
  let bubbleTimer = null;
  let lastActivity = performance.now();
  let lastZzz = 0;
  let lastScribble = 0;
  let lastPage = 0;
  let lastWaitSound = 0;
  let lastDash = -1e9;
  let lastPounce = -1e9;
  let landTimer = null;
  let blinkTimer = null;
  let spoutTimer = null;
  let clickTimer = null;           // 单击/双击判别：双击只走招牌动作，避免连发三次状态硬重置导致闪烁

  function currentVisual(now) {
    if (reaction && now < reaction.until) return reaction.name;
    reaction = null;
    if (dragInfo && dragInfo.moved) return "dangle";
    if (pet.look && follow && now < follow.until) return "look";
    follow = null;
    if (pet.look && glance) return "look";
    let state = null;
    for (const t of activeTasks.values()) state = t.state;
    if (state) return state;
    if (now - lastActivity > DOZE_MS) return "doze";
    return "idle";
  }

  function showBubble(text, ms) {
    if (!text) return;
    bubbleEl.textContent = text;
    bubbleEl.classList.add("show");
    if (bubbleTimer) clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(() => bubbleEl.classList.remove("show"), ms || 2400);
  }

  // ---------- 音效 ----------
  let soundOn = localStorage.getItem(LS_SOUND) !== "0";
  let audioCtx = null;
  let audioUnlocked = false;   // 浏览器要求首次用户手势后才能出声
  let sampleRaw = [];          // 当前角色叫声音频原始数据
  let sampleBuf = [];          // 预解码缓冲（裁掉前导静音，零延迟）
  let replyRaw = null;
  let replyBuf = null;
  let clipRaw = [];            // 状态配音（TTS 台词）原始数据
  let clipBuf = [];
  let clipState = [];
  let voiceSrc = null;
  let voiceStopTimer = null;
  const fallbackAudio = new Audio();
  fallbackAudio.preload = "auto";
  fallbackAudio.volume = 0.55;

  function ensureCtx() {
    if (!soundOn || !audioUnlocked) return null;
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
      return audioCtx;
    } catch (e) { return null; }
  }
  function trimOnset(buf) {
    const d = buf.getChannelData(0);
    const sr = buf.sampleRate;
    for (let i = 0; i < d.length; i++) {
      if (Math.abs(d[i]) > 0.02) return Math.max(0, i / sr - 0.04);
    }
    return 0;
  }
  function decodeSamples() {
    const ctx = ensureCtx();
    if (!ctx) return;
    sampleRaw.forEach((raw, i) => {
      if (!raw || sampleBuf[i]) return;
      ctx.decodeAudioData(raw.slice(0)).then((b) => {
        sampleBuf[i] = { buf: b, offset: trimOnset(b) };
      }).catch(() => {});
    });
    if (replyRaw && !replyBuf) {
      ctx.decodeAudioData(replyRaw.slice(0)).then((b) => {
        replyBuf = { buf: b, offset: trimOnset(b) };
      }).catch(() => {});
    }
  }
  function decodeClips() {
    const ctx = ensureCtx();
    if (!ctx) return;
    clipRaw.forEach((raw, i) => {
      if (!raw || clipBuf[i]) return;
      ctx.decodeAudioData(raw.slice(0)).then((b) => {
        clipBuf[i] = { buf: b, offset: trimOnset(b) };
      }).catch(() => {});
    });
  }
  function loadVoice() {
    sampleRaw = []; sampleBuf = []; replyRaw = null; replyBuf = null;
    clipRaw = []; clipBuf = []; clipState = [];
    stopVoice();
    const urls = (pet.voice && pet.voice.samples) || [];
    urls.forEach((url, i) => {
      fetch(url).then((r) => r.arrayBuffer()).then((buf) => {
        sampleRaw[i] = buf;
        if (i === 0) {
          fallbackAudio.src = URL.createObjectURL(new Blob([buf], { type: url.endsWith(".mp3") ? "audio/mpeg" : "audio/mp4" }));
          fallbackAudio.load();
        }
        decodeSamples();
      }).catch(() => { if (i === 0) { fallbackAudio.src = url; fallbackAudio.load(); } });
    });
    if (pet.voice && pet.voice.reply) {
      fetch(pet.voice.reply).then((r) => r.arrayBuffer()).then((buf) => {
        replyRaw = buf; decodeSamples();
      }).catch(() => {});
    }
    ((pet.voice && pet.voice.clips) || []).forEach((c, i) => {
      clipState[i] = c.s;
      fetch(c.u).then((r) => r.arrayBuffer()).then((buf) => {
        clipRaw[i] = buf; decodeClips();
      }).catch(() => {});
    });
  }
  document.addEventListener("pointerdown", () => {
    audioUnlocked = true;
    lastActivity = performance.now();
    ensureCtx();
    decodeSamples();
    decodeClips();
  }, { once: false });

  function tone(freqA, freqB, dur, type, gain) {
    const ctx = ensureCtx();
    if (!ctx) return;
    try {
      const osc = ctx.createOscillator();
      const g = ctx.createGain();
      osc.type = type || "sine";
      osc.frequency.setValueAtTime(freqA, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(freqB, ctx.currentTime + dur);
      g.gain.setValueAtTime(gain || 0.12, ctx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + dur);
      osc.connect(g).connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + dur);
    } catch (e) { /* 音频不可用时忽略 */ }
  }
  function noise(dur, freq, gain) {
    const ctx = ensureCtx();
    if (!ctx) return;
    try {
      const len = Math.floor(ctx.sampleRate * dur);
      const buf = ctx.createBuffer(1, len, ctx.sampleRate);
      const data = buf.getChannelData(0);
      const attack = Math.floor(ctx.sampleRate * 0.008);   // 柔和起音，避免"枪声"感
      for (let i = 0; i < len; i++) {
        const env = Math.min(i / attack, 1) * (1 - i / len);
        data[i] = (Math.random() * 2 - 1) * env;
      }
      const src = ctx.createBufferSource();
      src.buffer = buf;
      const filter = ctx.createBiquadFilter();
      filter.type = "bandpass";
      filter.frequency.value = freq;
      filter.Q.value = 0.8;
      const g = ctx.createGain();
      g.gain.value = gain;
      src.connect(filter).connect(g).connect(ctx.destination);
      src.start();
    } catch (e) { /* ignore */ }
  }
  function ding() { tone(880, 1318, 0.16, "sine", 0.1); setTimeout(() => tone(1318, 1760, 0.14, "sine", 0.08), 110); }
  function pop() { tone(420, 180, 0.09, "triangle", 0.1); }
  function sadTone() { tone(330, 196, 0.35, "sine", 0.08); }
  // 猫叫：两段滑音模拟"喵~"
  function meow() {
    tone(620, 900, 0.16, "sawtooth", 0.05);
    setTimeout(() => tone(880, 420, 0.22, "sawtooth", 0.045), 150);
  }
  // 鲸鸣：低频长滑音 + 缓慢起落
  function whaleSong() {
    const ctx = ensureCtx();
    if (!ctx) return;
    try {
      const dur = 1.5;
      const osc = ctx.createOscillator();
      const g = ctx.createGain();
      osc.type = "sine";
      const t0 = ctx.currentTime;
      osc.frequency.setValueAtTime(160, t0);
      osc.frequency.linearRampToValueAtTime(90, t0 + 0.5);
      osc.frequency.linearRampToValueAtTime(240, t0 + 1.0);
      osc.frequency.linearRampToValueAtTime(120, t0 + dur);
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.linearRampToValueAtTime(0.12, t0 + 0.25);
      g.gain.linearRampToValueAtTime(0.0001, t0 + dur);
      osc.connect(g).connect(ctx.destination);
      osc.start(); osc.stop(t0 + dur);
    } catch (e) { /* ignore */ }
  }
  // 铅笔轻扫：三笔柔和起落的小沙沙声，不是爆破噪点
  function scribble() {
    const ctx = ensureCtx();
    if (!ctx) return;
    try {
      const dur = 0.42;
      const len = Math.floor(ctx.sampleRate * dur);
      const buf = ctx.createBuffer(1, len, ctx.sampleRate);
      const data = buf.getChannelData(0);
      for (let i = 0; i < len; i++) {
        const t = i / ctx.sampleRate;
        const stroke = Math.max(0, Math.sin(Math.PI * ((t % 0.14) / 0.14)));
        data[i] = (Math.random() * 2 - 1) * stroke * stroke;
      }
      const src = ctx.createBufferSource();
      src.buffer = buf;
      const filter = ctx.createBiquadFilter();
      filter.type = "bandpass";
      filter.frequency.value = 2600;
      filter.Q.value = 0.7;
      const g = ctx.createGain();
      g.gain.value = 0.022;
      src.connect(filter).connect(g).connect(ctx.destination);
      src.start();
    } catch (e) { /* ignore */ }
  }
  function pageTurn() { noise(0.16, 700, 0.03); }
  function playMotif(state, gain) {
    const v = pet.voice || {};
    const notes = (v.motifs || {})[state];
    if (!notes || !soundOn || !audioUnlocked) return false;
    try {
      ensureCtx();
      const t = v.timbre || "sine";
      const g = gain == null ? 0.055 : gain;
      const glide = t === "sawtooth";
      const hit = (f) => glide ? tone(f * 1.3, f, 0.16, t, g) : tone(f, f * 1.02, 0.14, t, g);
      notes.forEach((n, i) => setTimeout(() => {
        if (Array.isArray(n)) n.forEach(hit); else hit(n);
      }, i * 120));
      return true;
    } catch (_) { return false; }
  }
  function stopVoice() {
    if (voiceStopTimer) { clearTimeout(voiceStopTimer); voiceStopTimer = null; }
    if (voiceSrc) { try { voiceSrc.stop(); } catch (e) {} voiceSrc = null; }
    try { fallbackAudio.pause(); } catch (e) {}
  }
  function playBuffer(entry, playS) {
    const ctx = ensureCtx();
    stopVoice();
    if (ctx && entry) {
      voiceSrc = ctx.createBufferSource();
      voiceSrc.buffer = entry.buf;
      const dur = Math.min(playS, entry.buf.duration - entry.offset);
      const g = ctx.createGain();
      const t0 = ctx.currentTime;
      g.gain.setValueAtTime(0.55, t0);
      g.gain.setValueAtTime(0.55, t0 + Math.max(0.1, dur - 0.15));
      g.gain.linearRampToValueAtTime(0.0001, t0 + dur);
      voiceSrc.connect(g).connect(ctx.destination);
      voiceSrc.start(0, entry.offset, dur + 0.05);
      voiceStopTimer = setTimeout(stopVoice, (dur + 0.1) * 1000);
      return true;
    }
    return false;
  }
  function playVoice(maxS) {
    if (!soundOn || !audioUnlocked) return;
    const playS = Math.min((pet.voice && pet.voice.playS) || 2.6, maxS || Infinity);
    const ready = sampleBuf.filter(Boolean);
    if (ready.length) {
      playBuffer(ready[Math.floor(Math.random() * ready.length)], playS);
      return;
    }
    if (playClip("laugh")) return;
    if (playMotif("laugh")) return;
    if (pet.voice && pet.voice.synth === "meow") { meow(); return; }
    if (pet.voice && pet.voice.synth === "whale") { whaleSong(); return; }
    if (pet.voice && pet.voice.synth === "chime") {
      tone(1046, 1568, 0.18, "sine", 0.07);
      setTimeout(() => tone(1568, 2093, 0.16, "sine", 0.06), 130);
      return;
    }
    if (pet.voice && pet.voice.synth === "hum") {
      tone(220, 330, 0.3, "triangle", 0.06);
      setTimeout(() => tone(330, 262, 0.34, "triangle", 0.05), 260);
      return;
    }
    try {
      fallbackAudio.currentTime = 0;
      fallbackAudio.play().catch(() => {});
      voiceStopTimer = setTimeout(stopVoice, playS * 1000);
    } catch (e) { /* ignore */ }
  }
  function playReply() {
    if (!soundOn || !audioUnlocked || !replyBuf) return;
    playBuffer(replyBuf, 2.0);
  }
  // 状态配音：按状态随机挑一条已解码台词；没有则返回 false 走合成音降级
  function playClip(state, maxS) {
    if (!soundOn || !audioUnlocked) return false;
    const idx = [];
    clipBuf.forEach((b, i) => { if (b && clipState[i] === state) idx.push(i); });
    if (!idx.length) return false;
    return playBuffer(clipBuf[idx[Math.floor(Math.random() * idx.length)]], Math.min(2.6, maxS || 2.6));
  }
  function syncPetToggles() {
    const hidden = root.classList.contains("naiwa-hidden");
    document.querySelectorAll("[data-pet-toggle]").forEach((b) => {
      b.setAttribute("aria-checked", String(!hidden));
      const label = b.querySelector("[data-pet-show-label]");
      if (label) label.textContent = hidden ? "已收起" : "显示中";
    });
    document.querySelectorAll("[data-pet-sound]").forEach((b) => {
      b.setAttribute("aria-checked", String(soundOn));
      const label = b.querySelector("[data-pet-sound-label]");
      if (label) label.textContent = soundOn ? "音效开" : "音效关";
    });
  }
  function setSound(on) {
    soundOn = !!on;
    localStorage.setItem(LS_SOUND, soundOn ? "1" : "0");
    if (!soundOn) stopVoice();
    syncPetToggles();
    if (soundOn) { ensureCtx(); decodeSamples(); pop(); }
  }
  // 设置页 / 左侧栏的桌宠开关
  document.addEventListener("click", (e) => {
    const t = e.target && e.target.closest ? e.target.closest("[data-pet-toggle],[data-pet-sound]") : null;
    if (!t) return;
    if (t.hasAttribute("data-pet-toggle")) setHidden(root.classList.contains("naiwa-hidden") ? false : true);
    else setSound(!soundOn);
  }, true);

  // ---------- 立绘模式：表情立绘切换 ----------
  function poseImageFor(name) {
    const im = pet.images || {};
    const si = pet.stateImages || {};
    if (name === "search" || name === "runleft") name = "run";
    if (name === "dangle" && si.wave) name = "wave";
    if (si[name]) return si[name];
    if (name === "laugh") return im.laugh || im.shout || im.stand;
    if (name === "wave" || name === "proud") return im.shout || im.stand;
    if (name === "fly") return im.flyShout || im.fly || im.stand;
    if (name === "doze") return im.sleep || im.blink || im.stand;
    return im.stand;
  }
  function setPoseImage(url) {
    if (url) spriteEl.style.backgroundImage = 'url("' + url + '")';
  }
  function scheduleBlink() {
    if (blinkTimer) clearTimeout(blinkTimer);
    blinkTimer = setTimeout(() => {
      if (pet.mode === "pose" && !document.hidden && !root.classList.contains("naiwa-hidden")
          && !reaction && !activeTasks.size && pet.images && pet.images.blink) {
        setPoseImage(pet.images.blink);
        setTimeout(() => { if (lastVisual === "idle") setPoseImage(pet.images.stand); }, 180);
      }
      scheduleBlink();
    }, 3800 + Math.random() * 3200);
  }

  // ---------- 状态进入 ----------
  // 动作停留时长：非循环态取完整周期，循环态取反应窗口；配音不得超过它
  function dwellS(name) {
    const def = pet.states[name];
    const animS = def && !def.loop ? def.frames / def.fps * (def.loops || 1) : Infinity;
    const reactS = ((pet.reactionMs && pet.reactionMs[name]) || 1600) / 1000;
    return Math.min(animS, reactS);
  }
  let clickVoiceAt = 0;
  // 单击配音优先窗：刚播了 click 配音时，反应态改用音动机，避免后播顶掉先播
  function stateClip(name) {
    if (performance.now() - clickVoiceAt < 400) return false;
    return playClip(name, dwellS(name) + 0.12);
  }
  function onEnter(name, now) {
    // sheet 角色没有的立绘态（如 dance）映射到招牌跳笑，避免"气泡说转圈、身体站 idle"的错配
    // look 是注视帧态（不在 states 里），帧由渲染循环处理，不能当缺失态兜底
    if (pet.mode === "sheet" && name !== "look" && !pet.states[name] && !(pet.poses && pet.poses[name])) {
      name = pet.states.laugh ? "laugh" : "idle";
    }
    animStart = now;
    const prevState = root.dataset.state;
    root.dataset.state = name;
    // 声画同步：离开任何状态立即停上一段配音，避免"动作停了声音还在响"
    if (prevState && prevState !== name) stopVoice();
    if (pet.mode === "sheet" && name !== "look" && !(pet.poses && pet.poses[name])) applyStrip(name);
    else { spriteEl.style.animation = "none"; stripState = ""; }
    if (pet.bubbles[name]) showBubble(pet.bubbles[name]);
    if (pet.mode === "pose") {
      setPoseImage(poseImageFor(name));
      if (spoutTimer) { clearTimeout(spoutTimer); spoutTimer = null; }
      // 蓝鲸跃出水面：弧顶喷水
      if (name === "laugh" && pet.images && pet.images.spout) {
        spoutTimer = setTimeout(() => {
          setPoseImage(pet.images.spout);
          spoutTimer = setTimeout(() => { if (lastVisual === "laugh") setPoseImage(poseImageFor("laugh")); }, 650);
        }, 420);
      }
    }
    if (name === "laugh") playVoice(dwellS("laugh") + 0.12);
    else if (name === "wave" || name === "proud") { ding(); if (!stateClip(name)) playMotif(name); }
    else if (name === "sad") { sadTone(); if (!stateClip("sad")) playMotif("sad"); }
    else if (name === "think" || name === "wait") { if (!stateClip(name)) playMotif(name); }
    else if (ACTS[name]) { pop(); playMotif(ACTS[name].motif); }
    else if (name === "rewrite") scribble();
    else if (name === "dangle") pop();
    else if (name === "play" || name === "knead") meow();
    else if (name === "fly" || name === "dance") { pop(); playMotif("dance"); }
    else if (name === "doze") playMotif("doze");
  }

  // ---------- 渲染循环 ----------
  // 逐帧动画改由 CSS steps() 驱动：JS 不再每帧写 background-position，
  // 避免 60fps 样式写入导致的整层反复光栅化（Windows 上表现为一闪一闪）
  const STRIP_KF = "pet-strip-frames";
  // 复合动作：逐帧行 + 一条 transform 运动曲线叠加，动作更丰富且过渡平滑
  const ACTS = {
    hop:     { kf: "pet-act-hop",     motif: "proud" },
    spin:    { kf: "pet-act-spin",    motif: "dance" },
    bow:     { kf: "pet-act-bow",     motif: "wave" },
    peek:    { kf: "pet-act-peek",    motif: "click" },
    stretch: { kf: "pet-act-stretch", motif: "doze" },
  };
  const CALM_LOOPS = { idle: 1, wait: 1, think: 1, doze: 1 };
  (function injectStripKeyframes() {
    const cw = 192 * SCALE;
    const st = document.createElement("style");
    st.textContent = "@keyframes " + STRIP_KF + " { from { background-position-x: 0px; } to { background-position-x: var(--strip-end, -" + (8 * cw) + "px); } }";
    document.head.appendChild(st);
  })();
  let stripState = "";
  function applyStrip(name) {
    const def = pet.states[name] || pet.states.idle;
    setFrame(def.row, 0);
    if (reducedMotion) { spriteEl.style.animation = "none"; stripState = ""; return; }
    const key = name + ":" + def.row + ":" + def.fps + ":" + (def.loop ? "L" : (def.loops || 1));
    if (key === stripState) return;
    stripState = key;
    const dur = (def.frames / def.fps).toFixed(3);
    const iter = def.loop ? "infinite" : String(def.loops || 1);
    // 关键帧终点必须等于"帧数×格宽"：奶蛙 idle7/wave4/laugh5 等非8帧行若按8帧宽步进，
    // steps() 会落在格间（如 -164.6px），露出邻帧残影（看起来像多只宠物叠影）
    spriteEl.style.setProperty("--strip-end", (-def.frames * pet.cellW * SCALE) + "px");
    spriteEl.style.animation = "none";
    void spriteEl.offsetWidth;   // 仅在状态切换时重置动画计时
    const cycles = def.loop ? 1 : (def.loops || 1);
    const actDur = (def.frames / def.fps * cycles).toFixed(3);
    let list = STRIP_KF + " " + dur + "s steps(" + def.frames + ") " + iter;
    if (def.act && ACTS[def.act]) list += ", " + ACTS[def.act].kf + " " + actDur + "s ease-in-out 1";
    // 安静循环态叠加呼吸微动：掩盖逐帧步进感，慢而不僵
    else if (def.loop && CALM_LOOPS[name]) list += ", pet-breath 3.2s ease-in-out infinite";
    spriteEl.style.animation = list;
  }
  spriteEl.addEventListener("animationend", () => {
    if (reaction && lastVisual === reaction.name) reaction = null;
  });
  function setFrame(row, col) {
    const cw = pet.cellW * SCALE, ch = pet.cellH * SCALE;
    spriteEl.style.backgroundPosition = (-col * cw) + "px " + (-row * ch) + "px";
  }
  function tick(now) {
    requestAnimationFrame(tick);
    if (document.hidden || root.classList.contains("naiwa-hidden")) return;

    // 看门狗：点击后接口失败、任务没真正建立时自动收回姿势
    for (const [kind, t] of activeTasks) {
      if (!t.confirmed && now - t.at > UNCONFIRMED_MS) activeTasks.delete(kind);
    }

    const name = currentVisual(now);
    if (name !== lastVisual) { lastVisual = name; onEnter(name, now); }

    // 任务进行中的环境音：写字沙沙 / 诊断翻书
    if (name === "rewrite" && now - lastScribble > 2000) { lastScribble = now; scribble(); }
    if (name === "diagnose" && now - lastPage > 2600) { lastPage = now; pageTurn(); }
    if (activeTasks.size && now - lastWaitSound > 3200) { lastWaitSound = now; playMotif("wait", 0.03); }
    if (name === "doze" && now - lastZzz > 9000) { lastZzz = now; showBubble(pet.bubbles.doze || "Zzz…", 2600); }

    if (pet.mode === "pose") return;              // 立绘动作交给 CSS
    if (pet.poses && pet.poses[name]) return;     // 奶蛙定制姿势交给 CSS

    if (name === "look" && pet.look) {
      const base = pet.look.row;
      if (follow && now < follow.until) {
        setFrame(base + Math.floor(follow.frame / 8), follow.frame % 8);
        return;
      }
      if (glance) {
        const idx = Math.floor((now - glance.start) / 420);
        if (idx >= glance.frames.length) { glance = null; return; }
        const f = glance.frames[idx];
        setFrame(base + Math.floor(f / 8), f % 8);
      }
      return;
    }

    // 帧推进交给 CSS steps() 动画（onEnter 已挂载），JS 只负责状态机与音效
    if (reducedMotion) setFrame((pet.states[name] || pet.states.idle).row, 0);
  }
  requestAnimationFrame(tick);

  // 待机时偶尔左右瞥（仅有注视帧的角色）
  if (!reducedMotion) {
    setInterval(() => {
      if (!pet.look) return;
      if (document.hidden || reaction || glance || follow || activeTasks.size) return;
      if (root.classList.contains("naiwa-hidden")) return;
      if (performance.now() - lastActivity > DOZE_MS) return;
      const pool = Array.from({ length: pet.look.frames }, (_, i) => i);
      const frames = [];
      const n = 4 + Math.floor(Math.random() * 3);
      for (let i = 0; i < n; i++) {
        frames.push(pool.splice(Math.floor(Math.random() * pool.length), 1)[0]);
      }
      glance = { frames, start: performance.now() };
    }, 6500);
  }

  // 鼠标移动：有注视帧的角色眼神跟随；哈基米则扑向靠近的鼠标
  if (!reducedMotion) {
    let lastFollow = 0;
    document.addEventListener("mousemove", (e) => {
      const now = performance.now();
      lastActivity = now;
      if (now - lastFollow < 60) return;
      lastFollow = now;
      if (activeTasks.size || reaction || dragInfo) return;
      if (root.classList.contains("naiwa-hidden")) return;
      const rect = root.getBoundingClientRect();
      const dx = e.clientX - (rect.left + rect.width / 2);
      const dy = e.clientY - (rect.top + rect.height / 2);
      if (pet.look) {
        if (Math.abs(dx) + Math.abs(dy) < 10) return;
        const theta = Math.atan2(dx, -dy);           // 以正上为 0、顺时针
        const frame = ((Math.round(theta / (Math.PI / 8)) % 16) + 16) % 16;
        follow = { frame, until: now + FOLLOW_MS };
        glance = null;
      } else if (pet.pounce) {
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 150 && dist > 24 && now - lastPounce > POUNCE_COOLDOWN) {
          lastPounce = now;
          react(pet.pounce);
        }
      }
    }, { passive: true });
  }

  // 滚动页面时偶尔原地跑两步（有冷却，不打扰）
  // 页面滚动发生在内部容器上，用捕获阶段监听任意滚动源
  let lastScrollTop = 0;
  let lastScrollTarget = null;
  document.addEventListener("scroll", (e) => {
    const now = performance.now();
    const target = e.target === document ? window : e.target;
    const top = target === window ? window.scrollY : target.scrollTop;
    const prev = lastScrollTarget === target ? lastScrollTop : 0;
    lastScrollTarget = target;
    lastScrollTop = top;
    const dy = top - prev;
    lastActivity = now;
    if (reducedMotion || !dy || activeTasks.size || reaction) return;
    if (now - lastDash < DASH_COOLDOWN) return;
    if (root.classList.contains("naiwa-hidden")) return;
    lastDash = now;
    react(dy > 0 ? "run" : "runleft");
  }, { passive: true, capture: true });

  // ---------- 任务挂钩 ----------
  function beginTask(kind, confirmed) {
    const state = pet.task[kind];
    if (!state) return;
    glance = null;
    follow = null;
    reaction = null;
    lastActivity = performance.now();
    activeTasks.delete(kind);
    activeTasks.set(kind, { state, confirmed: !!confirmed, at: performance.now() });
  }
  document.addEventListener("click", (e) => {
    const b = e.target && e.target.closest ? e.target.closest("button") : null;
    if (!b || b.disabled) return;
    const kind = BTN_TASK[b.id];
    if (kind) { beginTask(kind, false); return; }
    const cancelKind = BTN_CANCEL[b.id];
    if (cancelKind) {
      activeTasks.delete(cancelKind);
      lastActivity = performance.now();
    }
  }, true);
  document.addEventListener("change", (e) => {
    if (e.target && e.target.id === "paper-file" && e.target.files && e.target.files.length) {
      beginTask("upload", false);
    }
  }, true);

  // ---------- 拖拽与点击 ----------
  let pos = null;
  try { pos = JSON.parse(localStorage.getItem(LS_POS) || "null"); } catch (e) { pos = null; }
  const SCALE_MIN = 0.6, SCALE_MAX = 2;
  let scale = 1;
  function syncSizeUI() {
    const pct = Math.round(scale * 100);
    document.querySelectorAll("#pet-size").forEach((el) => { el.value = String(pct); });
    document.querySelectorAll("[data-pet-size-label]").forEach((el) => { el.textContent = pct + "%"; });
  }
  function applyPos() {
    if (!pos) return;
    const w = 168 * scale, h = 168 * scale;
    pos.x = Math.max(48 - w, Math.min(pos.x, window.innerWidth - w - 8));
    pos.y = Math.max(8, Math.min(pos.y, window.innerHeight - h - 8));
    root.style.left = pos.x + "px";
    root.style.top = pos.y + "px";
    root.style.right = "auto";
    root.style.bottom = "auto";
  }
  // 体型缩放：整体 transform 缩放，内部动作动画按比例跟随，放大后依旧灵动
  function setScale(next) {
    next = Math.max(SCALE_MIN, Math.min(SCALE_MAX, Math.round(next * 20) / 20));
    if (next === scale) { syncSizeUI(); return; }
    if (!pos) {          // 未拖拽过：把 CSS 右下锚点转成坐标，缩放才不跑位
      const r = root.getBoundingClientRect();
      pos = { x: r.left, y: r.top };
    }
    if (pos) {           // 保持脚底中心锚点，缩放时不跳位
      pos.x += 84 * (scale - next);
      pos.y += 168 * (scale - next);
    }
    scale = next;
    localStorage.setItem(LS_SCALE, String(scale));
    root.style.transform = "scale(" + scale + ")";
    applyPos();
    syncSizeUI();
  }
  let savedScale = parseFloat(localStorage.getItem(LS_SCALE));
  if (!isFinite(savedScale)) savedScale = 1;
  setScale(savedScale);
  applyPos();
  window.addEventListener("resize", applyPos);
  // 宠物身上滚轮缩放（不带动页面滚动）
  root.addEventListener("wheel", (e) => {
    e.preventDefault();
    e.stopPropagation();
    setScale(scale * (e.deltaY < 0 ? 1.1 : 1 / 1.1));
    showBubble("体型 " + Math.round(scale * 100) + "%", 900);
  }, { passive: false });
  document.addEventListener("input", (e) => {
    if (e.target && e.target.id === "pet-size") setScale(Number(e.target.value) / 100);
  });

  stage.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const rect = root.getBoundingClientRect();
    dragInfo = { dx: e.clientX - rect.left, dy: e.clientY - rect.top, moved: false, id: e.pointerId };
    lastActivity = performance.now();
    try { stage.setPointerCapture(e.pointerId); } catch (err) {}
  });
  stage.addEventListener("pointermove", (e) => {
    if (!dragInfo || e.pointerId !== dragInfo.id) return;
    const rect = root.getBoundingClientRect();
    const nx = e.clientX - dragInfo.dx;
    const ny = e.clientY - dragInfo.dy;
    if (!dragInfo.moved && Math.abs(nx - rect.left) + Math.abs(ny - rect.top) < 5) return;
    dragInfo.moved = true;
    pos = { x: nx, y: ny };
    applyPos();
  });
  stage.addEventListener("pointerup", (e) => {
    if (!dragInfo || e.pointerId !== dragInfo.id) return;
    const moved = dragInfo.moved;
    dragInfo = null;
    lastActivity = performance.now();
    if (moved) {
      localStorage.setItem(LS_POS, JSON.stringify(pos));
      pop();
      showBubble("站稳~");
      root.classList.add("naiwa-landing");
      if (landTimer) clearTimeout(landTimer);
      landTimer = setTimeout(() => root.classList.remove("naiwa-landing"), 340);
    } else {
      // 延迟 260ms 判别双击：双击期间取消单击反应，整次双击只产生一次状态切换
      if (clickTimer) clearTimeout(clickTimer);
      clickTimer = setTimeout(() => { clickTimer = null; clickReact(); }, 260);
    }
  });
  stage.addEventListener("pointercancel", () => { dragInfo = null; });
  stage.addEventListener("dblclick", () => {
    if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; }
    react("laugh");
  });
  function clickReact() {
    const name = pet.clickReactions[Math.floor(Math.random() * pet.clickReactions.length)];
    react(name);
    if (name === "wave") pop();
    const hasClip = ((pet.voice && pet.voice.clips) || []).some((c) => c.s === name);
    if (!hasClip && playClip("click", 1.8)) clickVoiceAt = performance.now();
    else if (!playMotif("click") && pet.voice && pet.voice.reply && Math.random() < 0.4) playReply();
    showBubble(pet.clickLines[Math.floor(Math.random() * pet.clickLines.length)]);
  }

  // ---------- 收起 / 唤出 ----------
  function setHidden(hidden) {
    root.classList.toggle("naiwa-hidden", hidden);
    badge.classList.toggle("show", hidden);
    localStorage.setItem(LS_HIDDEN, hidden ? "1" : "0");
    syncPetToggles();
  }
  badge.addEventListener("click", () => { setHidden(false); react("wave"); });
  if (localStorage.getItem(LS_HIDDEN) === "1") setHidden(true);
  syncPetToggles();

  // ---------- 形象切换 ----------
  function thumbStyle(id) {
    const p = PETS[id];
    if (p.mode === "sheet") {
      // 缩略图整格缩放：完整呈现待机帧，而不是裁到局部
      const s = 64 / p.cellW;
      return "background-image:url('" + p.sheet + "');background-size:" +
        (p.cols * p.cellW * s) + "px " + (p.rows * p.cellH * s) + "px;background-position:0px 0px;";
    }
    return "background-image:url('" + p.images.stand + "');background-size:contain;background-position:center bottom;background-repeat:no-repeat;";
  }
  function badgeStyle(id) {
    const p = PETS[id];
    if (p.mode === "sheet") {
      return "background-image:url('" + p.sheet + "');background-size:" +
        (p.cols * p.cellW * 0.25) + "px " + (p.rows * p.cellH * 0.25) + "px;background-position:-14px -10px;";
    }
    return "background-image:url('" + p.images.stand + "');background-size:contain;background-position:center bottom;background-repeat:no-repeat;";
  }
  function buildGallery() {
    const box = document.getElementById("pet-gallery");
    if (!box) return;
    box.innerHTML = "";
    PET_IDS.forEach((id) => {
      const p = PETS[id];
      const b = document.createElement("button");
      b.type = "button";
      b.className = "pet-card";
      b.dataset.petId = id;
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(id === petId));
      b.innerHTML = '<span class="pet-card-thumb" style="' + thumbStyle(id) + '"></span>' +
        '<span class="pet-card-name">' + p.label + '</span>' +
        '<span class="pet-card-tag">' + p.tag + '</span>';
      b.addEventListener("click", () => setPet(id));
      box.appendChild(b);
    });
  }
  function syncGallery() {
    document.querySelectorAll(".pet-card").forEach((b) => {
      b.setAttribute("aria-checked", String(b.dataset.petId === petId));
    });
  }
  function applyPetVisual() {
    root.dataset.pet = petId;
    root.dataset.mode = pet.mode;
    root.dataset.sig = pet.signature || "";
    // 清掉旧定制姿势立绘
    stage.querySelectorAll(".naiwa-pose").forEach((n) => n.remove());
    if (pet.mode === "sheet") {
      const cw = pet.cellW * SCALE, ch = pet.cellH * SCALE;
      spriteEl.style.backgroundImage = 'url("' + pet.sheet + '")';
      spriteEl.style.backgroundSize = (pet.cols * cw) + "px " + (pet.rows * ch) + "px";
      spriteEl.style.backgroundRepeat = "no-repeat";
      setFrame(0, 0);
      if (pet.poses) {
        Object.keys(pet.poses).forEach((k) => {
          const img = document.createElement("img");
          img.className = "naiwa-pose naiwa-pose-" + k;
          img.src = pet.poses[k];
          img.alt = "";
          img.draggable = false;
          stage.appendChild(img);
        });
      }
    } else {
      spriteEl.style.backgroundSize = "contain";
      spriteEl.style.backgroundPosition = "center bottom";
      spriteEl.style.backgroundRepeat = "no-repeat";
      setPoseImage(pet.images.stand);
    }
    badge.style.cssText = badgeStyle(petId);
    badge.title = "唤出" + pet.label;
    // 素材加载失败时静默隐藏，不影响主功能
    const probe = new Image();
    probe.onerror = () => { root.style.display = "none"; badge.style.display = "none"; };
    probe.src = pet.mode === "sheet" ? pet.sheet : pet.images.stand;
  }
  function setPet(id) {
    if (!PETS[id] || id === petId) return;
    petId = id;
    pet = PETS[id];
    localStorage.setItem(LS_PET, id);
    reaction = null; glance = null; follow = null;
    activeTasks.clear();
    lastVisual = "";
    stripState = "";
    applyPetVisual();
    loadVoice();
    syncGallery();
    showBubble(pet.greet);
    react("wave");
  }
  buildGallery();
  applyPetVisual();
  loadVoice();
  scheduleBlink();

  // 待机时偶尔自娱自乐：立绘角色转圈；逐帧角色按人设池跳/转/鞠躬/探头/伸懒腰
  if (!reducedMotion) {
    setInterval(() => {
      if (document.hidden || reaction || activeTasks.size || dragInfo) return;
      if (root.classList.contains("naiwa-hidden")) return;
      if (performance.now() - lastActivity > DOZE_MS) return;
      if (pet.mode === "pose") { if (Math.random() < 0.35) react("dance"); return; }
      const pool = pet.idleActs || [];
      if (pool.length && Math.random() < 0.4) react(pool[Math.floor(Math.random() * pool.length)]);
    }, 18000);
  }

  // ---------- 对外 API ----------
  function react(name) {
    const ms = (pet.reactionMs && pet.reactionMs[name]) || (name === "dance" ? 1600 : 1400);
    reaction = { name, until: performance.now() + ms };
    lastActivity = performance.now();
  }
  window.NaiwaPet = {
    taskStart(kind) { opStart(kind); },
    opStart,
    opEnd,
    taskEnd(kind) {
      activeTasks.delete(kind);
      lastActivity = performance.now();
    },
    react,
    show() { setHidden(false); },
    hide() { setHidden(true); },
    setSound,
    setPet,
    setScale,
    get scale() { return scale; },
    get pet() { return petId; },
    get pets() { return PET_IDS.slice(); },
    get soundOn() { return soundOn; },
    get hidden() { return root.classList.contains("naiwa-hidden"); },
    get state() { return currentVisual(performance.now()); },
  };

  // 出场打招呼（首次手势前音频会被浏览器拦截，静默降级）
  setTimeout(() => { if (!activeTasks.size) { react("wave"); showBubble(pet.greet); } }, 60);
})();