# UniCalli Mobile UI v3

## 设计结论

手机端不再复用桌面组件布局。桌面是“横向展卷”，手机是“掌中册页”。两端共享生成函数、Session、偏好数据与事件总线，界面 DOM 和交互层独立。

## 手机端状态

### 1. Compose / 题写

只呈现题写任务：汉字输入、书家、书体、落笔。高级参数和偏好不常驻屏幕。

### 2. Generating / 生成

题写界面退出。屏幕只呈现当前正在生成的段落、墨迹预览和状态。没有 composer、drawer、desktop toolbar。

### 3. Reading / 阅卷

每一段占据一个完整阅读视口，上下滑动并使用 scroll snap。作品没有卡片阴影和 SaaS 式边框；辅助信息压到册页底部，只保留段号、文字和“重写”。

### Settings / 设置

设置是独立页面，会替换题写页面显示。它不会作为 drawer / bottom sheet / backdrop 覆盖题写或作品。

## 文件职责

- `unicalli_ui.css`：桌面和 >= 768px 紧凑桌面规则。
- `unicalli_ui.js`：桌面横卷与通用浏览器事件。
- `unicalli_mobile_ui.py`：手机端独立舞台 HTML renderer。
- `unicalli_mobile.css`：<= 767px 手机界面的唯一布局来源。
- `unicalli_mobile.js`：手机状态机、阅卷 DOM、重写、汉字过滤与 viewport 处理。
- `app.py`：挂载两套 Gradio 组件树，并把同一生成流镜像到两端。

## 解决的根本问题

- 删除手机端对 `#composer-dock`、`#side-drawers`、`#stage-controls` 的覆盖。
- 删除旧的 `640 / 480 / 390` 多套手机断点和 mobile-v2 fixed overlay。
- 手机端不再使用 bottom sheet、drawer backdrop、动态 reserved-bottom。
- 软键盘只更新实际 visual viewport 高度，不参与组件之间的互相避让计算。
- 手机生成和重写拥有独立 bridge component，不依赖桌面隐藏输入。

## 验证

已执行：

- `python -m py_compile app.py unicalli_ui.py unicalli_mobile_ui.py`
- `node --check unicalli_ui.js`
- `node --check unicalli_mobile.js`
- CSS 括号/注释/字符串结构检查
- 在缺少项目 dataset 的上传包中，通过 mock `ProjectData` 成功实例化完整 Gradio Blocks 配置
- 通过 mock generation stream 验证 generation dual stream 输出宽度、Session 建立、task_completed 事件
- 通过 mock reroll stream 验证 reroll_started / reroll_completed 和双端输出

上传包本身不包含 `dataset/author_fonts_summary.csv`，所以无法直接使用真实项目数据启动完整应用；UI 构建校验使用 mock ProjectData 完成。
