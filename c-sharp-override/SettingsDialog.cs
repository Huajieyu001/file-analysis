using CleanDup.Models;

namespace CleanDup;

/// <summary>
/// Settings dialog with tabs: Scan Options + File Extensions.
/// </summary>
public partial class SettingsDialog : Form
{
    public HashSet<string>? ResultExts; // null = scan all
    public bool ResultFullHash;
    public int ResultMinSize;

    private readonly CheckBox _cbFullHash, _cbScanAll;
    private readonly TextBox _tbMinSize;
    private readonly Dictionary<string, CheckBox> _extVars = new();

    public SettingsDialog(HashSet<string> currentExts, bool fullHash, int minSize)
    {
        Text = "设置"; Size = new Size(620, 560); StartPosition = FormStartPosition.CenterParent;
        FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false; MinimizeBox = false;
        ShowInTaskbar = false;

        var tabs = new TabControl { Dock = DockStyle.Fill };
        Controls.Add(tabs);

        // ── Tab 1: Scan options ──
        var tab1 = new TabPage("扫描选项");
        _cbFullHash = new CheckBox { Text = "全量哈希（精确模式，速度慢）", Checked = fullHash, Location = new Point(16, 16), AutoSize = true };
        tab1.Controls.Add(_cbFullHash);
        tab1.Controls.Add(new Label { Text = "最小文件大小 (MB):", Location = new Point(16, 48), AutoSize = true });
        _tbMinSize = new TextBox { Text = minSize.ToString(), Location = new Point(150, 45), Width = 80 };
        tab1.Controls.Add(_tbMinSize);
        tabs.TabPages.Add(tab1);

        // ── Tab 2: File extensions ──
        var tab2 = new TabPage("文件后缀");
        _cbScanAll = new CheckBox { Text = "扫描所有文件（忽略后缀过滤）", Checked = currentExts.Count == 0, Location = new Point(16, 12), AutoSize = true };
        _cbScanAll.CheckedChanged += (s, e) => UpdateExtEnabled();
        tab2.Controls.Add(_cbScanAll);

        // Category buttons
        int catX = 16;
        foreach (var name in ExtensionCategories.All.Keys)
        {
            var btn = new Button { Text = name, Location = new Point(catX, 40), Size = new Size(80, 26), FlatStyle = FlatStyle.Flat };
            btn.Click += (s, e) => ToggleBigCat(name);
            tab2.Controls.Add(btn);
            catX += 88;
        }

        // Extension tree
        int y = 75;
        var panel = new Panel { Location = new Point(0, 72), Size = new Size(590, 400), AutoScroll = true };
        foreach (var (bigCat, subCats) in ExtensionCategories.All)
        {
            var bigCb = new CheckBox { Text = bigCat, Location = new Point(16, y - 72), AutoSize = true };
            bigCb.CheckedChanged += (s, e) => ToggleBigCatCb(bigCat, bigCb.Checked);
            panel.Controls.Add(bigCb);
            y += 24;

            foreach (var (subCat, exts) in subCats)
            {
                var subCb = new CheckBox { Text = subCat, Location = new Point(36, y - 72), AutoSize = true };
                subCb.Checked = exts.All(e => currentExts.Contains(e));
                subCb.CheckedChanged += (s, e) => ToggleSubCat(subCat, subCb.Checked);
                panel.Controls.Add(subCb);
                y += 22;

                int ex = 56;
                foreach (var ext in exts)
                {
                    var cb = new CheckBox { Text = ext, Location = new Point(ex, y - 72), AutoSize = true };
                    cb.Checked = currentExts.Contains(ext);
                    _extVars[ext] = cb;
                    panel.Controls.Add(cb);
                    ex += 68;
                    if (ex > 540) { ex = 56; y += 22; }
                }
                y += 24;
            }
        }
        tab2.Controls.Add(panel);

        // ── Buttons ──
        var btnCancel = new Button { Text = "取消", Location = new Point(440, 490), Size = new Size(80, 30), FlatStyle = FlatStyle.Flat };
        btnCancel.Click += (s, e) => { DialogResult = DialogResult.Cancel; Close(); };
        Controls.Add(btnCancel);
        var btnSave = new Button { Text = "保存设置", Location = new Point(526, 490), Size = new Size(80, 30),
            BackColor = Color.FromArgb(0x3B, 0x82, 0xF6), ForeColor = Color.White, FlatStyle = FlatStyle.Flat };
        btnSave.Click += (s, e) =>
        {
            ResultFullHash = _cbFullHash.Checked;
            ResultMinSize = int.TryParse(_tbMinSize.Text, out var v) ? v : 200;
            ResultExts = _cbScanAll.Checked ? null :
                _extVars.Where(kv => kv.Value.Checked).Select(kv => kv.Key).ToHashSet();
            DialogResult = DialogResult.OK;
            Close();
        };
        Controls.Add(btnSave);
    }

    private void ToggleBigCat(string name)
    {
        bool anyOn = false;
        foreach (var (subCat, exts) in ExtensionCategories.All[name])
            if (exts.Any(e => _extVars.ContainsKey(e) && _extVars[e].Checked)) { anyOn = true; break; }
        foreach (var (_, exts) in ExtensionCategories.All[name])
            foreach (var e in exts)
                if (_extVars.ContainsKey(e)) _extVars[e].Checked = !anyOn;
    }

    private void ToggleBigCatCb(string bigCat, bool chk) =>
        ToggleBigCat(bigCat); // reuse toggle logic

    private void ToggleSubCat(string subCat, bool chk)
    {
        foreach (var (_, subCats) in ExtensionCategories.All)
            if (subCats.TryGetValue(subCat, out var exts))
                foreach (var e in exts)
                    if (_extVars.ContainsKey(e)) _extVars[e].Checked = chk;
    }

    private void UpdateExtEnabled()
    {
        foreach (var cb in _extVars.Values) cb.Enabled = !_cbScanAll.Checked;
    }
}
