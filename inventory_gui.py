# AIGC START
"""海康多台录像机通道盘点 — 一键拉取参数、跨机查重、对照 IP 表。"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from inventory_core import (
    IP_RE,
    CompareReport,
    InventoryReport,
    build_camera_web_url,
    compare_ip_table,
    export_channels_tsv,
    export_compare_tsv,
    fetch_multi_inventory,
    open_web_url,
    parse_ip_table,
    parse_nvr_list,
)

SAMPLE_NVR = (
    "http://192.168.22.252\n"
    "http://192.168.22.253\n"
    "# 也可写：地址\\t用户名\\t密码\\t备注\n"
    "# 192.168.22.254\tadmin\tYourPass\t三号楼\n"
)

SAMPLE_IP = (
    "192.168.22.50\t2层仓库内监控靠近工装房\n"
    "192.168.22.47\t2层仓库内工装房旁边的小房\n"
    "192.168.22.46\t2层仓库内监控靠近仓库入口2\n"
    "192.168.22.132\t15层6号（走廊）\n"
)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("海康多机通道盘点（参数 / 查重 / IP 对照）")
        self.geometry("1180x880")
        self.minsize(1000, 740)

        self._running = False
        self._report: InventoryReport | None = None
        self._compare: CompareReport | None = None
        self._filter_nvr = tk.StringVar(value="全部")

        pad = {"padx": 12, "pady": 4}
        root = ttk.Frame(self)
        root.pack(fill=tk.BOTH, expand=True, **pad)

        tip = (
            "可填多台录像机（每行一台）。列表里显示为「252录像机」这类短名，避免机型名重复。\n"
            "双击任意表格里的监控 IP → 打开摄像头网页；勾选「尝试带账号打开」可尽量自动带登录（部分浏览器会拦截）。"
        )
        ttk.Label(root, text=tip, justify=tk.LEFT).pack(anchor="w", pady=(0, 6))

        form = ttk.Frame(root)
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        self.nvr_user = tk.StringVar(value="admin")
        self.nvr_pass = tk.StringVar(value="")
        self.timeout = tk.StringVar(value="25")
        self.embed_auth = tk.BooleanVar(value=True)

        self._pair(form, 0, 0, "默认用户名", self.nvr_user)
        self._pair(form, 0, 2, "默认密码", self.nvr_pass, show="*")
        self._pair(form, 1, 0, "超时(秒)", self.timeout)
        ttk.Checkbutton(
            form,
            text="双击 IP 时尝试带账号打开网页（http://用户:密码@IP）",
            variable=self.embed_auth,
        ).grid(row=1, column=2, columnspan=2, sticky="w", pady=2)

        ttk.Label(root, text="录像机列表（每行一台）").pack(anchor="w", pady=(8, 2))
        self.nvr_text = tk.Text(root, height=5, font=("Consolas", 10))
        self.nvr_text.pack(fill=tk.X)
        self.nvr_text.insert("1.0", SAMPLE_NVR)

        btns = ttk.Frame(root)
        btns.pack(fill=tk.X, pady=8)
        self.fetch_btn = ttk.Button(
            btns, text="一键拉取全部录像机", command=self.fetch_all
        )
        self.fetch_btn.pack(side=tk.LEFT)
        ttk.Button(btns, text="检查录像机名单", command=self.preview_nvrs).pack(
            side=tk.LEFT, padx=8
        )
        ttk.Button(btns, text="导出通道 TSV", command=self.export_channels).pack(
            side=tk.LEFT
        )
        ttk.Button(btns, text="只看重复 IP", command=self.focus_duplicates).pack(
            side=tk.LEFT, padx=8
        )
        ttk.Button(btns, text="显示全部通道", command=self.show_all_channels).pack(
            side=tk.LEFT
        )
        ttk.Label(btns, text="筛选录像机").pack(side=tk.LEFT, padx=(16, 4))
        self.nvr_filter = ttk.Combobox(
            btns,
            textvariable=self._filter_nvr,
            values=("全部",),
            state="readonly",
            width=22,
        )
        self.nvr_filter.pack(side=tk.LEFT)
        self.nvr_filter.bind("<<ComboboxSelected>>", lambda _e: self.show_all_channels())

        self.summary = tk.StringVar(value="尚未拉取 · 请填写录像机列表后一键拉取")
        ttk.Label(
            root,
            textvariable=self.summary,
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 4))

        self.dup_banner = tk.StringVar(value="")
        self.dup_lbl = ttk.Label(
            root,
            textvariable=self.dup_banner,
            font=("Microsoft YaHei UI", 10, "bold"),
            foreground="#a33",
        )
        self.dup_lbl.pack(anchor="w")

        nb = ttk.Notebook(root)
        nb.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

        # --- tab: live channels ---
        tab_ch = ttk.Frame(nb)
        nb.add(tab_ch, text="全部通道")
        ch_frm = ttk.Frame(tab_ch)
        ch_frm.pack(fill=tk.BOTH, expand=True)
        cols = ("录像机", "通道", "IP", "名称", "协议", "管理端口", "源通道", "码流")
        self.tree = ttk.Treeview(ch_frm, columns=cols, show="headings", height=12)
        widths = (140, 60, 130, 200, 90, 70, 60, 70)
        for c, w in zip(cols, widths):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="w")
        sy = ttk.Scrollbar(ch_frm, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sy.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("dup", foreground="#a33")
        self.tree.tag_configure("empty", foreground="#888888")
        self.tree.bind("<Double-1>", self._on_channel_double_click)

        # --- tab: per-NVR status ---
        tab_nvr = ttk.Frame(nb)
        nb.add(tab_nvr, text="各机状态")
        nvr_frm = ttk.Frame(tab_nvr)
        nvr_frm.pack(fill=tk.BOTH, expand=True)
        ncols = ("状态", "短名", "地址", "通道数", "设备型号名", "说明")
        self.nvr_tree = ttk.Treeview(nvr_frm, columns=ncols, show="headings", height=12)
        for c, w in zip(ncols, (70, 100, 200, 70, 180, 280)):
            self.nvr_tree.heading(c, text=c)
            self.nvr_tree.column(c, width=w, anchor="w")
        sy3 = ttk.Scrollbar(nvr_frm, orient=tk.VERTICAL, command=self.nvr_tree.yview)
        self.nvr_tree.configure(yscrollcommand=sy3.set)
        self.nvr_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy3.pack(side=tk.RIGHT, fill=tk.Y)
        self.nvr_tree.tag_configure("ok", foreground="#0a7a32")
        self.nvr_tree.tag_configure("fail", foreground="#a33")

        # --- tab: IP compare ---
        tab_cmp = ttk.Frame(nb)
        nb.add(tab_cmp, text="IP 表对照")

        ttk.Label(
            tab_cmp,
            text="粘贴自己的 IP 表（对照全部已拉取录像机；Excel：IP、名称…）",
        ).pack(anchor="w", pady=(4, 2))
        self.ip_text = tk.Text(tab_cmp, height=7, font=("Consolas", 10))
        self.ip_text.pack(fill=tk.X)
        self.ip_text.insert("1.0", SAMPLE_IP)

        cmp_btns = ttk.Frame(tab_cmp)
        cmp_btns.pack(fill=tk.X, pady=6)
        ttk.Button(cmp_btns, text="从文件导入", command=self.import_ip_file).pack(
            side=tk.LEFT
        )
        ttk.Button(cmp_btns, text="清空 IP 表", command=self.clear_ip_table).pack(
            side=tk.LEFT, padx=8
        )
        ttk.Button(cmp_btns, text="检查名单", command=self.preview_ip_table).pack(
            side=tk.LEFT
        )
        self.compare_btn = ttk.Button(
            cmp_btns, text="对照：已存在 / 未添加", command=self.run_compare
        )
        self.compare_btn.pack(side=tk.LEFT, padx=8)
        ttk.Button(cmp_btns, text="导出对照结果", command=self.export_compare).pack(
            side=tk.LEFT
        )

        self.compare_summary = tk.StringVar(value="先拉取通道，再粘贴 IP 表后点对照")
        ttk.Label(tab_cmp, textvariable=self.compare_summary).pack(anchor="w", pady=2)

        cmp_frm = ttk.Frame(tab_cmp)
        cmp_frm.pack(fill=tk.BOTH, expand=True)
        ccols = ("状态", "IP", "表中名称", "所在通道", "通道名称")
        self.cmp_tree = ttk.Treeview(cmp_frm, columns=ccols, show="headings", height=10)
        for c, w in zip(ccols, (80, 130, 200, 220, 200)):
            self.cmp_tree.heading(c, text=c)
            self.cmp_tree.column(c, width=w, anchor="w")
        sy2 = ttk.Scrollbar(cmp_frm, orient=tk.VERTICAL, command=self.cmp_tree.yview)
        self.cmp_tree.configure(yscrollcommand=sy2.set)
        self.cmp_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy2.pack(side=tk.RIGHT, fill=tk.Y)
        self.cmp_tree.tag_configure("exist", foreground="#0a7a32")
        self.cmp_tree.tag_configure("missing", foreground="#a33")
        self.cmp_tree.tag_configure("dup", foreground="#b8860b")
        self.cmp_tree.tag_configure("orphan", foreground="#5555aa")
        self.cmp_tree.bind("<Double-1>", self._on_compare_double_click)

        ttk.Label(root, text="运行日志").pack(anchor="w", pady=(8, 2))
        self.log = tk.Text(
            root, height=7, font=("Consolas", 9), bg="#1e1e1e", fg="#dddddd"
        )
        self.log.pack(fill=tk.BOTH, expand=False)
        self.log.configure(state=tk.DISABLED)

        self.status = tk.StringVar(value="就绪")
        ttk.Label(root, textvariable=self.status).pack(anchor="w", pady=(4, 0))

    def _pair(
        self,
        parent: ttk.Frame,
        row: int,
        col: int,
        label: str,
        var: tk.StringVar,
        show: str | None = None,
    ) -> None:
        ttk.Label(parent, text=label, width=12).grid(
            row=row, column=col, sticky="w", pady=2, padx=(0, 4)
        )
        kw: dict = {"textvariable": var}
        if show is not None:
            kw["show"] = show
        ttk.Entry(parent, **kw).grid(row=row, column=col + 1, sticky="ew", pady=2)

    def append_log(self, msg: str) -> None:
        def _do() -> None:
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, msg + "\n")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)

        self.after(0, _do)

    def _open_camera_ip(self, ip: str, cam_user: str = "") -> None:
        ip = (ip or "").strip()
        if not IP_RE.fullmatch(ip):
            messagebox.showinfo("提示", "这一行没有有效的监控 IP")
            return
        user = (cam_user or self.nvr_user.get()).strip() or "admin"
        password = self.nvr_pass.get()
        embed = self.embed_auth.get()
        try:
            url = build_camera_web_url(
                ip, user=user, password=password, embed_auth=embed
            )
        except ValueError as exc:
            messagebox.showwarning("IP", str(exc))
            return
        self.append_log(f"打开摄像头网页：{ip}" + ("（带账号）" if embed else ""))
        self.status.set(f"已打开 {ip} 网页")
        threading.Thread(target=lambda: open_web_url(url), daemon=True).start()

    def _tree_ip_at_event(self, tree: ttk.Treeview, event: tk.Event, ip_col: str) -> str:
        row = tree.identify_row(event.y)
        if not row:
            return ""
        vals = tree.item(row, "values")
        cols = list(tree["columns"])
        try:
            idx = cols.index(ip_col)
        except ValueError:
            return ""
        if idx >= len(vals):
            return ""
        return str(vals[idx]).strip()

    def _on_channel_double_click(self, event: tk.Event) -> None:
        # Prefer the IP column under cursor; else use selected row's IP
        col = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)
        if not row:
            return
        vals = self.tree.item(row, "values")
        cols = list(self.tree["columns"])
        ip = ""
        if col:
            try:
                cidx = int(col.replace("#", "")) - 1
                if 0 <= cidx < len(cols) and cols[cidx] == "IP" and cidx < len(vals):
                    ip = str(vals[cidx]).strip()
            except ValueError:
                pass
        if not ip:
            try:
                ip = str(vals[cols.index("IP")]).strip()
            except (ValueError, IndexError):
                ip = ""
        if ip in {"-", ""}:
            return
        # channel user_name not shown; use default
        self._open_camera_ip(ip)

    def _on_compare_double_click(self, event: tk.Event) -> None:
        ip = self._tree_ip_at_event(self.cmp_tree, event, "IP")
        if not ip or ip in {"-", ""}:
            return
        self._open_camera_ip(ip)

    def _parsed_nvrs(self):
        try:
            timeout = float(self.timeout.get().strip() or "25")
        except ValueError:
            timeout = 25.0
        return parse_nvr_list(
            self.nvr_text.get("1.0", tk.END),
            default_user=self.nvr_user.get().strip(),
            default_password=self.nvr_pass.get(),
            timeout=timeout,
        )

    def preview_nvrs(self) -> None:
        result = self._parsed_nvrs()
        if not result.items:
            messagebox.showwarning(
                "检查录像机",
                "没有解析到有效录像机。\n每行一台：地址，或 地址\\t用户\\t密码\\t备注\n"
                + "\n".join(result.skipped[:8]),
            )
            return
        lines = [
            f"{o.label}  ←  {o.base_url}  （用户 {o.nvr_user}）" for o in result.items[:30]
        ]
        more = (
            ""
            if len(result.items) <= 30
            else f"\n… 还有 {len(result.items) - 30} 台"
        )
        warn = ""
        if result.warnings:
            warn = "\n\n警告：\n" + "\n".join(result.warnings[:8])
        skip = f"\n\n已忽略 {len(result.skipped)} 行" if result.skipped else ""
        messagebox.showinfo(
            "检查录像机",
            f"将拉取 {len(result.items)} 台：\n\n" + "\n".join(lines) + more + warn + skip,
        )

    def _selected_channels(self, report: InventoryReport, only_dup: bool = False):
        dup_ips = {g.ip for g in report.duplicates}
        filt = self._filter_nvr.get().strip()
        rows = report.channels
        if filt and filt != "全部":
            rows = [ch for ch in rows if ch.nvr_display == filt or ch.nvr_url == filt]
        if only_dup:
            rows = [ch for ch in rows if ch.ip in dup_ips]
        return rows, dup_ips

    def _fill_channel_tree(
        self, report: InventoryReport, only_dup: bool = False
    ) -> None:
        for i in self.tree.get_children():
            self.tree.delete(i)
        rows, dup_ips = self._selected_channels(report, only_dup=only_dup)
        for ch in rows:
            tags: list[str] = []
            if ch.ip in dup_ips:
                tags.append("dup")
            if not ch.ip:
                tags.append("empty")
            self.tree.insert(
                "",
                tk.END,
                values=(
                    ch.nvr_display,
                    f"D{ch.channel_id}",
                    ch.ip or "-",
                    ch.name or "",
                    ch.protocol or "",
                    ch.manage_port or "",
                    ch.src_input_port or "",
                    ch.stream_type or "",
                ),
                tags=tuple(tags),
            )

    def _fill_nvr_tree(self, report: InventoryReport) -> None:
        for i in self.nvr_tree.get_children():
            self.nvr_tree.delete(i)
        for r in report.per_nvr:
            short = r.opt.label or (
                r.channels[0].nvr_display if r.channels else r.opt.base_url
            )
            if r.ok:
                self.nvr_tree.insert(
                    "",
                    tk.END,
                    values=(
                        "成功",
                        short,
                        r.opt.base_url,
                        len(r.channels),
                        r.device_name or "",
                        "",
                    ),
                    tags=("ok",),
                )
            else:
                self.nvr_tree.insert(
                    "",
                    tk.END,
                    values=(
                        "失败",
                        short,
                        r.opt.base_url,
                        0,
                        "",
                        r.error[:200],
                    ),
                    tags=("fail",),
                )

    def _apply_report(self, report: InventoryReport) -> None:
        self._report = report
        labels = ["全部"]
        for r in report.per_nvr:
            if r.ok:
                name = r.opt.label or (
                    r.channels[0].nvr_display if r.channels else r.opt.base_url
                )
                if name not in labels:
                    labels.append(name)
        self.nvr_filter.configure(values=labels)
        if self._filter_nvr.get() not in labels:
            self._filter_nvr.set("全部")

        self._fill_channel_tree(report)
        self._fill_nvr_tree(report)
        self.summary.set(
            f"{report.nvr_ok} 台成功"
            + (f" / {report.nvr_fail} 台失败" if report.nvr_fail else "")
            + f"  ·  监控总数 {report.total} 路  ·  "
            f"有效 IP {report.with_ip}  ·  唯一 IP {report.unique_ips}"
            + (f"  ·  无 IP {report.empty_ip}" if report.empty_ip else "")
        )
        if report.duplicates:
            parts = []
            for g in report.duplicates[:10]:
                ds = "、".join(c.channel_label() for c in g.channels)
                parts.append(f"{g.ip}→{ds}")
            more = (
                ""
                if len(report.duplicates) <= 10
                else f" …等共 {len(report.duplicates)} 组"
            )
            self.dup_banner.set(
                f"⚠ IP 重复 {len(report.duplicates)} 组（含跨机）："
                + "；".join(parts)
                + more
            )
            self.dup_lbl.configure(foreground="#a33")
        else:
            self.dup_banner.set("✓ 无重复 IP（含跨机）")
            self.dup_lbl.configure(foreground="#0a7a32")

    def fetch_all(self) -> None:
        if self._running:
            messagebox.showinfo("提示", "正在执行，请稍候")
            return
        parsed = self._parsed_nvrs()
        if not parsed.items:
            messagebox.showwarning(
                "缺少录像机",
                "没有可拉取的录像机。请每行写一台地址，并确认默认用户名密码已填。",
            )
            return
        if parsed.warnings and not messagebox.askyesno(
            "名单有警告",
            "\n".join(parsed.warnings[:10]) + "\n\n仍要继续？",
        ):
            return

        self._running = True
        self.fetch_btn.configure(state=tk.DISABLED)
        self.status.set(f"拉取中… {len(parsed.items)} 台")
        self.append_log("=" * 60)
        for s in parsed.skipped[:12]:
            self.append_log(s)

        targets = parsed.items

        def worker() -> None:
            try:
                report = fetch_multi_inventory(targets, on_log=self.append_log)

                def done() -> None:
                    self._apply_report(report)
                    for g in report.duplicates:
                        ds = "、".join(
                            f"{c.channel_label()}({c.name or '-'})" for c in g.channels
                        )
                        self.append_log(f"重复 IP {g.ip}：{ds}")
                    if not report.duplicates:
                        self.append_log("未发现重复 IP")
                    self.status.set(
                        f"拉取完成 · {report.nvr_ok}/{report.nvr_ok + report.nvr_fail} 台 · "
                        f"{report.total} 路"
                    )
                    if report.nvr_fail and report.nvr_ok == 0:
                        messagebox.showerror(
                            "全部失败",
                            "所有录像机都拉取失败，请看日志。",
                        )
                    elif report.nvr_fail:
                        messagebox.showwarning(
                            "部分失败",
                            f"{report.nvr_fail} 台失败，{report.nvr_ok} 台成功。"
                            "详情见「各机状态」页。",
                        )

                self.after(0, done)
            except Exception as exc:  # noqa: BLE001
                self.append_log(f"拉取失败：{exc}")
                err = str(exc)
                self.after(0, lambda: messagebox.showerror("拉取失败", err))
                self.after(0, lambda: self.status.set("拉取失败"))
            finally:
                self.after(0, self._finished)

        threading.Thread(target=worker, daemon=True).start()

    def _finished(self) -> None:
        self._running = False
        self.fetch_btn.configure(state=tk.NORMAL)

    def focus_duplicates(self) -> None:
        if not self._report:
            messagebox.showinfo("提示", "请先一键拉取")
            return
        if not self._report.duplicates:
            messagebox.showinfo("重复 IP", "当前没有重复 IP")
            return
        self._fill_channel_tree(self._report, only_dup=True)
        self.status.set(
            f"仅显示重复 IP · {len(self._report.duplicates)} 组"
        )

    def show_all_channels(self) -> None:
        if not self._report:
            messagebox.showinfo("提示", "请先一键拉取")
            return
        self._fill_channel_tree(self._report, only_dup=False)
        filt = self._filter_nvr.get()
        self.status.set(
            f"显示通道 · 筛选「{filt}」· 共 {self._report.total} 路"
        )

    def export_channels(self) -> None:
        if not self._report:
            messagebox.showinfo("提示", "请先一键拉取")
            return
        path = filedialog.asksaveasfilename(
            title="导出通道参数",
            defaultextension=".tsv",
            filetypes=[("TSV / Excel 可打开", "*.tsv"), ("全部", "*.*")],
            initialfile="nvr_channels.tsv",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(export_channels_tsv(self._report.channels))
        self.append_log(f"已导出通道：{path}")
        messagebox.showinfo("导出", f"已保存：\n{path}")

    def clear_ip_table(self) -> None:
        self.ip_text.delete("1.0", tk.END)

    def import_ip_file(self) -> None:
        path = filedialog.askopenfilename(
            title="导入 IP 表",
            filetypes=[
                ("文本 / CSV / TSV", "*.txt;*.csv;*.tsv"),
                ("全部", "*.*"),
            ],
        )
        if not path:
            return
        try:
            raw = open(path, "rb").read()
            for enc in ("utf-8-sig", "utf-8", "gbk", "gb2312"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                text = raw.decode("utf-8", errors="replace")
        except OSError as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        self.ip_text.delete("1.0", tk.END)
        self.ip_text.insert("1.0", text)
        self.append_log(f"已导入 IP 表：{path}")

    def preview_ip_table(self) -> None:
        result = parse_ip_table(self.ip_text.get("1.0", tk.END))
        if not result.items:
            messagebox.showwarning(
                "检查名单",
                "没有解析到有效 IP。\n请粘贴含 IP 的行（可加名称列）。\n"
                + "\n".join(result.skipped[:8]),
            )
            return
        lines = [f"{a.ip}  {a.name}" for a in result.items[:30]]
        more = (
            ""
            if len(result.items) <= 30
            else f"\n… 还有 {len(result.items) - 30} 条"
        )
        warn = ""
        if result.warnings:
            warn = "\n\n警告：\n" + "\n".join(result.warnings[:10])
        skip = f"\n\n已忽略 {len(result.skipped)} 行" if result.skipped else ""
        messagebox.showinfo(
            "检查名单",
            f"解析到 {len(result.items)} 个 IP：\n\n"
            + "\n".join(lines)
            + more
            + warn
            + skip,
        )

    def run_compare(self) -> None:
        if not self._report or not self._report.channels:
            messagebox.showinfo("提示", "请先「一键拉取全部录像机」")
            return
        parsed = parse_ip_table(self.ip_text.get("1.0", tk.END))
        if not parsed.items:
            messagebox.showwarning("缺少名单", "IP 表为空，请先粘贴或导入")
            return
        report = compare_ip_table(parsed.items, self._report.channels)
        self._compare = report

        for i in self.cmp_tree.get_children():
            self.cmp_tree.delete(i)
        for r in report.rows:
            tag = (
                "exist"
                if r.status == "已存在"
                else "missing"
                if r.status == "未添加"
                else "dup"
            )
            self.cmp_tree.insert(
                "",
                tk.END,
                values=(r.status, r.ip, r.name, r.channels, r.nvr_names),
                tags=(tag,),
            )
        for ch in report.orphan_on_nvr:
            self.cmp_tree.insert(
                "",
                tk.END,
                values=(
                    "仅录像机",
                    ch.ip,
                    "",
                    ch.channel_label(),
                    ch.name or "",
                ),
                tags=("orphan",),
            )

        orphan_n = len(report.orphan_on_nvr)
        self.compare_summary.set(
            f"表中 {len(parsed.items)} 条  ·  已存在 {report.exist_count}  ·  "
            f"未添加 {report.missing_count}"
            + (
                f"  ·  表中重复 {report.table_dup_count}"
                if report.table_dup_count
                else ""
            )
            + (f"  ·  仅录像机有 {orphan_n}" if orphan_n else "")
            + f"  ·  对照范围 {self._report.nvr_ok} 台机"
        )
        self.append_log(
            f"对照完成：已存在 {report.exist_count}，未添加 {report.missing_count}，"
            f"仅录像机 {orphan_n}"
        )
        for s in parsed.skipped[:8]:
            self.append_log(s)
        for w in parsed.warnings[:8]:
            self.append_log(w)
        self.status.set(
            f"对照完成 · 已存在 {report.exist_count} · 未添加 {report.missing_count}"
        )

    def export_compare(self) -> None:
        if not self._compare:
            messagebox.showinfo("提示", "请先做一次对照")
            return
        path = filedialog.asksaveasfilename(
            title="导出对照结果",
            defaultextension=".tsv",
            filetypes=[("TSV / Excel 可打开", "*.tsv"), ("全部", "*.*")],
            initialfile="ip_compare.tsv",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(export_compare_tsv(self._compare))
        self.append_log(f"已导出对照：{path}")
        messagebox.showinfo("导出", f"已保存：\n{path}")


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
# AIGC END
