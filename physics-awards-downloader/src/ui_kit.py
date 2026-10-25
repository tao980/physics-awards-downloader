# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""界面基础件：圆角卡片 / 药丸按钮 / 圆角输入框 / 勾选块 / 胶囊进度条。

tkinter 没有圆角，这里统一用 Canvas 画。所有组件只依赖标准库。

对外接口刻意贴近 ttk 的同名控件（invoke / cget("text") / configure(state=) /
instate / state），并且用 class_ 让 winfo_class() 仍报 TButton / TCheckbutton，
这样上层业务代码和既有自动化脚本不必区分"这是自绘的还是原生的"。
"""

import tkinter as tk
import tkinter.font as tkfont

FONT = "Microsoft YaHei UI"

# 各版本 Windows 自带的中文字体不一样：Win10/11 有 "Microsoft YaHei UI"，
# Win7 与部分精简版只有 "Microsoft YaHei" / "SimHei"。字体名不存在时
# Tk 会静默退到默认字体（西文），中文要么变方块要么字宽算错，所以启动时
# 按顺序挑第一个真实存在的族名。
_FONT_CANDIDATES = ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑",
                    "Microsoft JhengHei UI", "SimHei", "黑体", "SimSun", "宋体")


def resolve_font(root):
    """挑一个系统里真实存在的中文字体并记到 FONT，返回选中的族名。

    要在创建任何控件之前调用（各处的 font=(FONT, 9) 都是取当前值）。
    """
    global FONT
    try:
        fams = {}
        for f in tkfont.families(root):
            fams[f.lower()] = f
    except Exception:
        return FONT
    for name in _FONT_CANDIDATES:
        real = fams.get(name.lower())
        if real:
            FONT = real
            return real
    try:                       # 一个都没有就跟随系统默认字体，至少不会崩
        FONT = tkfont.nametofont("TkDefaultFont").actual("family")
    except Exception:
        pass
    return FONT

# ---------------------------------------------------------------- 配色
# 淡蓝主色 + 冷灰中性色；白色卡片浮在浅蓝灰底上，整体偏"纸面"质感。
PAGE = "#EDF2F9"          # 页面底色
CARD = "#FFFFFF"          # 卡片
LINE = "#E3EBF5"          # 卡片描边 / 分隔线
LINE_SOFT = "#EFF4FA"
INK = "#1D2A38"           # 标题
INK_BODY = "#3D4E60"      # 正文
MUTED = "#8496AA"         # 次要文字
FAINT = "#AAB9C8"         # 禁用 / 极弱

BLUE_50 = "#F4F9FF"
BLUE_100 = "#E7F1FD"
BLUE_200 = "#CFE2FA"
BLUE_300 = "#A8CAF2"
BLUE_500 = "#4A85D8"      # 主色
BLUE_600 = "#376FC4"
BLUE_700 = "#2F5FA9"
SEL = "#E2EDFC"           # 选中行
OK_CLR = "#2F8F63"
BAD_CLR = "#BE5147"
WARN_CLR = "#B2782A"
FIELD_BG = "#F5F8FC"      # 输入框底


def rr(cv, x1, y1, x2, y2, r, **kw):
    """在 canvas 上画圆角矩形，返回 item id。"""
    r = max(0, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, splinesteps=24, **kw)


def _try(w, **kw):
    try:
        w.configure(**kw)
    except tk.TclError:
        pass


# ---------------------------------------------------------------- 按钮
class RButton(tk.Frame):
    """圆角药丸按钮。

    kind: primary 实心主色 / soft 淡蓝 / ghost 描边 / quiet 纯文字 / danger 描边警示
    width 传了就是固定宽度，不传按文字自适应（改文字会自动重算宽度）。
    """

    DEFAULT_H = 34

    _SKIN = {
        "primary": ((BLUE_500, "", "#FFFFFF"), (BLUE_600, "", "#FFFFFF"),
                    (BLUE_700, "", "#FFFFFF"), ("#DFE9F8", "", "#AFC2D8")),
        "soft": ((BLUE_50, BLUE_200, BLUE_600), (BLUE_100, BLUE_300, BLUE_600),
                 (BLUE_200, BLUE_300, BLUE_600), ("#F4F7FB", LINE, "#BAC7D6")),
        "ghost": ((CARD, LINE, "#44576C"), (BLUE_50, BLUE_300, BLUE_600),
                  (BLUE_100, BLUE_300, BLUE_600), (CARD, LINE, "#BAC7D6")),
        "quiet": (("", "", MUTED), (BLUE_50, "", BLUE_600),
                  (BLUE_100, "", BLUE_600), ("", "", "#C3CFDB")),
        "danger": ((CARD, "#EDD8D6", BAD_CLR), ("#FDF4F3", "#E2C2BF", BAD_CLR),
                   ("#FAE9E7", "#E2C2BF", BLUE_700 and BAD_CLR), (CARD, LINE, "#C8B6B4")),
    }

    def __init__(self, master, text="", command=None, kind="ghost", width=None,
                 height=None, radius=None, bg=None, font=None):
        h = height or self.DEFAULT_H
        tk.Frame.__init__(self, master, class_="TButton", bd=0, highlightthickness=0,
                          bg=bg or CARD, width=width or 12, height=h)
        self.pack_propagate(False)
        self.grid_propagate(False)
        self._cv = tk.Canvas(self, bg=bg or CARD, highlightthickness=0, bd=0,
                             width=width or 12, height=h)
        self._cv.pack(fill="both", expand=True)
        self._kind = kind
        self._cmd = command
        self._text = text
        self._fontspec = font or (FONT, 9)
        self._f = tkfont.Font(font=self._fontspec)
        self._h = h
        self._r = radius if radius is not None else max(6, h // 3)
        self._fixed = width
        self._disabled = self._hover = self._press = False
        self._cv.bind("<Enter>", self._enter)
        self._cv.bind("<Leave>", self._leave)
        self._cv.bind("<ButtonPress-1>", self._down)
        self._cv.bind("<ButtonRelease-1>", self._up)
        self._cv.configure(cursor="hand2")
        self._sync()

    # ------------------------------------------------ 外观
    def _padx(self):
        return (12, 12) if self._kind == "quiet" else (19, 19)

    def _want(self):
        if self._fixed:
            return self._fixed
        a, b = self._padx()
        return self._f.measure(self._text) + a + b

    def _skin(self):
        rows = self._SKIN.get(self._kind) or self._SKIN["ghost"]
        if self._disabled:
            return rows[3]
        if self._press:
            return rows[2]
        if self._hover:
            return rows[1]
        return rows[0]

    def _sync(self):
        w = max(20, int(self._want()))
        tk.Frame.configure(self, width=w, height=self._h)
        self._cv.configure(width=w, height=self._h)
        self._draw()

    def _draw(self):
        cv = self._cv
        cv.delete("all")
        w = max(1, int(cv["width"]))
        h = max(1, int(cv["height"]))
        fill, outline, fg = self._skin()
        if fill or outline:
            rr(cv, 0.5, 0.5, w - 0.5, h - 0.5, self._r,
               fill=fill or "", outline=outline or "")
        cv.create_text(w / 2.0, h / 2.0, text=self._text, fill=fg, font=self._fontspec)

    # ------------------------------------------------ 交互
    def _enter(self, _e=None):
        self._hover = True
        self._draw()

    def _leave(self, _e=None):
        self._hover = self._press = False
        self._draw()

    def _down(self, _e=None):
        if self._disabled:
            return
        self._press = True
        self._draw()

    def _up(self, _e=None):
        fire = self._press and not self._disabled
        self._press = False
        self._draw()
        if fire:
            self.invoke()

    def invoke(self):
        if self._disabled:
            return
        if self._cmd is not None:
            self._cmd()

    # ------------------------------------------------ ttk 兼容接口
    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return "disabled" if self._disabled else "normal"
        return tk.Frame.cget(self, key)

    def state(self):
        return ("disabled",) if self._disabled else ("normal",)

    def instate(self, spec):
        if isinstance(spec, str):
            spec = (spec,)
        have = set(self.state())
        for s in spec:
            if s.startswith("!"):
                if s[1:] in have:
                    return False
            elif s not in have:
                return False
        return True

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw = dict(cnf, **kw)
            cnf = None
        elif isinstance(cnf, str) and cnf not in kw:
            return self.cget(cnf)
        kw.pop("cnf", None)
        redraw = False
        if "text" in kw:
            self._text = kw.pop("text")
            redraw = True
        if "state" in kw:
            st = str(kw.pop("state"))
            self._disabled = st not in ("normal", "active", "readonly")
            self._cv.configure(cursor="arrow" if self._disabled else "hand2")
            redraw = True
        if "command" in kw:
            self._cmd = kw.pop("command")
        if "width" in kw:
            try:
                self._fixed = int(kw.pop("width"))
            except (TypeError, ValueError):
                kw.pop("width", None)
            redraw = True
        if "height" in kw:
            try:
                self._h = int(kw.pop("height"))
            except (TypeError, ValueError):
                kw.pop("height", None)
            redraw = True
        if "kind" in kw:
            self._kind = kw.pop("kind")
            redraw = True
        if kw:
            tk.Frame.configure(self, **kw)
        if redraw:
            self._sync()
        return None

    config = configure


# ---------------------------------------------------------------- 勾选块
class RCheck(tk.Frame):
    """胶囊勾选块：选中时淡蓝底 + 蓝色对勾。语义等同 ttk.Checkbutton。"""

    def __init__(self, master, text="", variable=None, command=None,
                 height=28, bg=None, font=None):
        h = height
        tk.Frame.__init__(self, master, class_="TCheckbutton", bd=0,
                          highlightthickness=0, bg=bg or CARD, width=12, height=h)
        self.pack_propagate(False)
        self._cv = tk.Canvas(self, bg=bg or CARD, highlightthickness=0, bd=0,
                             width=12, height=h)
        self._cv.pack(fill="both", expand=True)
        self.variable = variable if variable is not None else tk.BooleanVar(value=False)
        self._text = text
        self._cmd = command
        self._fontspec = font or (FONT, 9)
        self._f = tkfont.Font(font=self._fontspec)
        self._h = h
        self._r = h // 2
        self._disabled = self._hover = False
        self._cv.bind("<Enter>", self._enter)
        self._cv.bind("<Leave>", self._leave)
        self._cv.bind("<ButtonRelease-1>", self._up)
        self._cv.configure(cursor="hand2")
        self._sync()

    def _want(self):
        return self._f.measure(self._text) + 44

    def _sync(self):
        w = max(40, int(self._want()))
        tk.Frame.configure(self, width=w, height=self._h)
        self._cv.configure(width=w, height=self._h)
        self._draw()

    def _draw(self):
        cv = self._cv
        cv.delete("all")
        w = max(1, int(cv["width"]))
        h = max(1, int(cv["height"]))
        on = bool(self.variable.get())
        if self._disabled:
            fill, line, fg = "#F5F7FA", LINE, "#BFCBD8"
            mark = "#C9D4E0"
        elif on:
            fill, line, fg = BLUE_50, BLUE_300, BLUE_600
            mark = BLUE_500
        elif self._hover:
            fill, line, fg = "#F0F6FE", BLUE_200, "#5A6E84"
            mark = BLUE_200
        else:
            fill, line, fg = FIELD_BG, LINE, "#66798E"
            mark = "#D2DEEB"
        rr(cv, 0.5, 0.5, w - 0.5, h - 0.5, self._r, fill=fill, outline=line)
        cy = h / 2.0
        d = 13
        cx = 13
        cv.create_oval(cx, cy - d / 2.0, cx + d, cy + d / 2.0,
                       fill=mark if on else "", outline=line if not on else "")
        if on:
            cv.create_text(cx + d / 2.0, cy, text="✓", fill="#FFFFFF",
                           font=(FONT, 7, "bold"))
        cv.create_text(cx + d + 7, cy, text=self._text, fill=fg,
                       font=self._fontspec, anchor="w")

    def _enter(self, _e=None):
        self._hover = True
        self._draw()

    def _leave(self, _e=None):
        self._hover = False
        self._draw()

    def _up(self, _e=None):
        self.invoke()

    def invoke(self):
        if self._disabled:
            return
        self.variable.set(not self.variable.get())
        self._draw()
        if self._cmd is not None:
            self._cmd()

    # ------------------------------------------------ ttk 兼容接口
    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return "disabled" if self._disabled else "normal"
        if key == "variable":
            return self.variable
        return tk.Frame.cget(self, key)

    def state(self):
        s = []
        if self.variable.get():
            s.append("selected")
        if self._disabled:
            s.append("disabled")
        if self._hover:
            s.append("active")
        return tuple(s)

    def instate(self, spec):
        if isinstance(spec, str):
            spec = (spec,)
        have = set(self.state())
        for s in spec:
            if s.startswith("!"):
                if s[1:] in have:
                    return False
            elif s not in have:
                return False
        return True

    def select(self):
        self.variable.set(True)
        self._draw()

    def deselect(self):
        self.variable.set(False)
        self._draw()

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw = dict(cnf, **kw)
            cnf = None
        kw.pop("cnf", None)
        redraw = False
        if "text" in kw:
            self._text = kw.pop("text")
            redraw = True
        if "state" in kw:
            self._disabled = str(kw.pop("state")) not in ("normal", "active")
            self._cv.configure(cursor="arrow" if self._disabled else "hand2")
            redraw = True
        if "variable" in kw:
            self.variable = kw.pop("variable")
            redraw = True
        if "command" in kw:
            self._cmd = kw.pop("command")
        if kw:
            tk.Frame.configure(self, **kw)
        if redraw:
            self._sync()
        return None

    config = configure


# ---------------------------------------------------------------- 进度条
class RProgress(tk.Canvas):
    """胶囊进度条；接口对齐 ttk.Progressbar 的 value / maximum。"""

    def __init__(self, master, height=6, bg=None, fill=None, track=None):
        tk.Canvas.__init__(self, master, bg=bg or CARD, height=height,
                           highlightthickness=0, bd=0)
        self._h = height
        self._v = 0.0
        self._max = 100.0
        self._fill = fill or BLUE_500
        self._track = track or "#E6EEF8"
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, value=None, maximum=None):
        if value is not None:
            self._v = value
        if maximum is not None:
            self._max = maximum
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height() or self._h
        if w <= 2:
            return
        rr(self, 0.5, 0.5, w - 0.5, h - 0.5, h / 2.0, fill=self._track, outline="")
        try:
            p = max(0.0, min(1.0, float(self._v) / max(1.0, float(self._max))))
        except (TypeError, ValueError):
            p = 0.0
        if p <= 0:
            return
        fw = max(h, w * p)
        rr(self, 0.5, 0.5, fw - 0.5, h - 0.5, h / 2.0, fill=self._fill, outline="")

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw = dict(cnf, **kw)
            cnf = None
        dirty = False
        if "value" in kw:
            try:
                self._v = float(kw.pop("value"))
            except (TypeError, ValueError):
                kw.pop("value", None)
            dirty = True
        if "maximum" in kw:
            try:
                self._max = max(1.0, float(kw.pop("maximum")))
            except (TypeError, ValueError):
                kw.pop("maximum", None)
            dirty = True
        if kw:
            tk.Canvas.configure(self, **kw)
            dirty = True
        if dirty:
            self._draw()
        return None

    config = configure


# ---------------------------------------------------------------- 卡片
class Card(tk.Frame):
    """圆角白卡片。

    expand=True 时跟随父容器撑满；否则高度由内容撑开。
    子控件放进 card.body（普通 Frame，CARD 底色）。背景那层圆角矩形画在
    一个 place 铺满的 Canvas 上，铺在 body 底下——body 内缩一个圆角半径，
    方角正好落在圆弧内侧，看不出接缝。
    """

    def __init__(self, master, radius=12, fill=CARD, bg=None, expand=False):
        tk.Frame.__init__(self, master, bg=bg or PAGE, bd=0, highlightthickness=0)
        self.r = radius
        self.fill = fill
        self._bg = bg or PAGE
        self._expand = expand
        self._cv = tk.Canvas(self, bg=self._bg, highlightthickness=0, bd=0)
        self._cv.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=fill, bd=0, highlightthickness=0)
        self.body.pack(fill="both", expand=True, padx=radius, pady=radius)
        self.bind("<Configure>", self._redraw)

    def _redraw(self, e):
        cv = self._cv
        cv.delete("bg")
        rr(cv, 0.5, 0.5, e.width - 1.5, e.height - 1.5, self.r,
           fill=self.fill, outline=LINE, tags="bg")


# ---------------------------------------------------------------- 圆角输入框
class RoundField(tk.Frame):
    """把 Entry / Combobox 放进一个圆角底里。

    做法：Canvas 画圆角矩形，控件嵌在里侧（x 缩进 ≥ 圆角半径），
    控件自身底色与底一致，于是方角藏在圆角之内，看不出来。
    """

    def __init__(self, master, factory, height=32, radius=9, padx=10,
                 width=None, bg=None, fill=FIELD_BG, line=LINE):
        tk.Frame.__init__(self, master, bg=bg or CARD, height=height,
                          bd=0, highlightthickness=0)
        if width:
            tk.Frame.configure(self, width=width)
        self.pack_propagate(False)
        self._cv = tk.Canvas(self, bg=bg or CARD, height=height,
                             highlightthickness=0, bd=0)
        self._cv.pack(fill="both", expand=True)
        self._h = height
        self._r = radius
        self._pad = padx
        self._fill = fill
        self._line = line
        self._focus = False
        self.child = factory(self._cv)
        _try(self.child, highlightthickness=0, bd=0)
        self._win = self._cv.create_window(padx, height / 2.0, window=self.child,
                                           anchor="w")
        self._cv.bind("<Configure>", self._layout)
        self._cv.bind("<Button-1>", lambda e: self.child.focus_set())
        for seq in ("<FocusIn>", "<FocusOut>"):
            try:
                self.child.bind(seq, self._on_focus, add="+")
            except tk.TclError:
                pass

    def _on_focus(self, e):
        self._focus = str(e.type) == "9"      # FocusIn
        self._layout()

    def _layout(self, _e=None):
        w = self._cv.winfo_width()
        h = self._cv.winfo_height() or self._h
        if w <= 2:
            return
        self._cv.delete("box")
        rr(self._cv, 0.5, 0.5, w - 0.5, h - 0.5, self._r,
           fill=self._fill, outline=BLUE_300 if self._focus else self._line,
           tags="box")
        self._cv.tag_lower("box")
        self._cv.coords(self._win, self._pad, h / 2.0)
        self._cv.itemconfigure(self._win, width=max(10, w - 2 * self._pad))


# ---------------------------------------------------------------- ttk 主题
def setup(root, dpi=1.0):
    """把 ttk 控件调成同一套观感（表格 / 下拉 / 滚动条）。"""
    from tkinter import ttk

    px = lambda v: int(round(v * dpi))  # noqa: E731
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass

    st.configure(".", background=CARD, foreground=INK_BODY, font=(FONT, 9))
    st.configure("TFrame", background=CARD)
    st.configure("Page.TFrame", background=PAGE)
    st.configure("TLabel", background=CARD, foreground=INK_BODY, font=(FONT, 9))
    st.configure("H1.TLabel", background=PAGE, foreground=INK, font=(FONT, 12, "bold"))
    st.configure("Sub.TLabel", background=PAGE, foreground=MUTED, font=(FONT, 9))
    st.configure("CardTitle.TLabel", background=CARD, foreground=INK,
                 font=(FONT, 10, "bold"))
    st.configure("Field.TLabel", background=CARD, foreground=MUTED, font=(FONT, 9))
    st.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=(FONT, 9))
    st.configure("Status.TLabel", background=CARD, foreground=INK_BODY, font=(FONT, 9))

    # 表格：去掉外框，表头淡蓝、行高放宽
    st.configure("Treeview", background=CARD, fieldbackground=CARD,
                 foreground=INK_BODY, font=(FONT, 9), rowheight=px(29),
                 borderwidth=0, relief="flat",
                 lightcolor=CARD, darkcolor=CARD, bordercolor=CARD)
    st.map("Treeview",
           background=[("selected", SEL)],
           foreground=[("selected", BLUE_700)])
    st.configure("Treeview.Heading", background=BLUE_50, foreground=MUTED,
                 font=(FONT, 8, "bold"), relief="flat", borderwidth=0,
                 padding=(px(7), px(8)),
                 lightcolor=LINE, darkcolor=LINE, bordercolor=LINE)
    st.map("Treeview.Heading", background=[("active", BLUE_100)])

    # 细滚动条：只留滑块
    try:
        st.layout("Slim.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {
                "sticky": "ns",
                "children": [("Vertical.Scrollbar.thumb",
                              {"expand": "1", "sticky": "nswe"})]})])
        st.configure("Slim.Vertical.TScrollbar", troughcolor=CARD,
                     background="#D4E1F0", bordercolor=CARD,
                     lightcolor="#D4E1F0", darkcolor="#D4E1F0",
                     arrowcolor=CARD, borderwidth=0, width=px(8))
        st.map("Slim.Vertical.TScrollbar",
               background=[("active", "#B9CEE6"), ("pressed", "#A2BBD8")])
    except tk.TclError:
        pass

    # 下拉框：无边框，底色与圆角底一致
    st.configure("Filter.TCombobox", fieldbackground=FIELD_BG, background=FIELD_BG,
                 foreground=INK_BODY, bordercolor=FIELD_BG, lightcolor=FIELD_BG,
                 darkcolor=FIELD_BG, arrowcolor=MUTED, arrowsize=px(12),
                 padding=(0, px(4)), borderwidth=0, relief="flat")
    st.map("Filter.TCombobox",
           fieldbackground=[("readonly", FIELD_BG), ("disabled", "#F0F3F8"),
                            ("!readonly", FIELD_BG)],
           foreground=[("disabled", FAINT)],
           selectbackground=[("readonly", FIELD_BG)],
           selectforeground=[("readonly", INK_BODY)],
           arrowcolor=[("disabled", "#CDD8E4")])

    st.configure("Path.TEntry", fieldbackground=FIELD_BG, foreground=INK,
                 bordercolor=FIELD_BG, lightcolor=FIELD_BG, darkcolor=FIELD_BG,
                 insertcolor=BLUE_500, padding=(0, px(3)), borderwidth=0)
    st.map("Path.TEntry", fieldbackground=[("disabled", "#F0F3F8")],
           foreground=[("disabled", FAINT)])

    st.configure("Num.TSpinbox", fieldbackground=FIELD_BG, foreground=INK,
                 bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                 arrowcolor=MUTED, arrowsize=px(10), padding=(px(4), px(3)),
                 borderwidth=1)
    st.map("Num.TSpinbox", fieldbackground=[("focus", CARD), ("!focus", FIELD_BG)],
           bordercolor=[("focus", BLUE_300)])

    # 下拉列表（原生 Listbox）也跟着配色
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", INK_BODY)
    root.option_add("*TCombobox*Listbox.selectBackground", SEL)
    root.option_add("*TCombobox*Listbox.selectForeground", BLUE_700)
    root.option_add("*TCombobox*Listbox.font", (FONT, 9))
    root.option_add("*TCombobox*Listbox.borderWidth", 1)
    return st
