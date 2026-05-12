using CleanDup.Models;
using Microsoft.VisualBasic.FileIO;
using System.Data;

namespace CleanDup;

/// <summary>
/// Main application window — WinForms UI.
/// Tabs: Duplicate Groups | Search | Folder Compare | Local Check | Empty Cleanup
/// </summary>
public partial class MainForm : Form
{
    private Database? _db;
    private readonly Deduplicator _dedup = new();
    private CancellationTokenSource? _scanCts;
    private bool _scanRunning;
    private HashSet<string> _activeExts = ExtensionCategories.DefaultExtensions();
    private bool _fullHashEnabled;
    private int _minSizeMb = 200;
    private List<DupGroup> _dupGroups = [];
    private DataTable _dupTable = new(), _searchTable = new(), _cmpTable = new(), _localTable = new(), _emptyTable = new();

    public MainForm()
    {
        InitializeComponent();
        LoadSettings();
        _db = new Database();
        _db.InitDb();
    }

    private void MainForm_Load(object sender, EventArgs e)
    {
        LoadDataAsync();
        BeginInvoke(() => StartScan(force: true)); // auto-scan on startup
    }

    // ═══════════════════════════════════════════════
    //  Settings
    // ═══════════════════════════════════════════════

    private void LoadSettings()
    {
        try
        {
            if (File.Exists(Config.SettingsPath))
            {
                var json = System.Text.Json.JsonDocument.Parse(File.ReadAllText(Config.SettingsPath));
                var root = json.RootElement;
                if (root.TryGetProperty("full_hash", out var fh)) _fullHashEnabled = fh.GetBoolean();
                if (root.TryGetProperty("min_size_mb", out var ms)) _minSizeMb = ms.GetInt32();
                if (root.TryGetProperty("scan_all", out var sa) && sa.GetBoolean())
                    _activeExts = []; // empty = scan all
                else if (root.TryGetProperty("extensions", out var exts))
                    _activeExts = exts.EnumerateArray().Select(e => e.GetString()!).ToHashSet();
            }
        }
        catch { }
    }

    private void SaveSettings()
    {
        var obj = new { full_hash = _fullHashEnabled, min_size_mb = _minSizeMb,
            extensions = _activeExts.ToArray() };
        File.WriteAllText(Config.SettingsPath,
            System.Text.Json.JsonSerializer.Serialize(obj, new System.Text.Json.JsonSerializerOptions { WriteIndented = true }));
    }

    private void BtnSettings_Click(object sender, EventArgs e)
    {
        using var dlg = new SettingsDialog(_activeExts, _fullHashEnabled, _minSizeMb);
        if (dlg.ShowDialog() != DialogResult.OK) return;
        _activeExts = dlg.ResultExts ?? [];
        _fullHashEnabled = dlg.ResultFullHash;
        _minSizeMb = dlg.ResultMinSize;
        SaveSettings();
        _scanCts?.Cancel();
        _scanRunning = false;
        BeginInvoke(() => StartScan(force: true));
    }

    // ═══════════════════════════════════════════════
    //  Scan
    // ═══════════════════════════════════════════════

    private async void StartScan(bool force)
    {
        if (_scanRunning) return;
        _scanRunning = true;
        _scanCts = new CancellationTokenSource();

        var drives = GetSelectedDrives();
        if (drives.Count == 0) { _scanRunning = false; return; }

        Invoke(() => { lblStatus.Text = "● 扫描中..."; lblStatus.ForeColor = Color.Orange; btnRefresh.Enabled = false; progressBar.Value = 0; });

        var exts = _activeExts.Count > 0 ? _activeExts : null;
        Config.MinFileSizeMb = _minSizeMb;
        _dedup.Progress += OnScanProgress;

        try
        {
            var groups = await Task.Run(() => _dedup.RunDedup(drives, _db!, force, exts, !_fullHashEnabled), _scanCts.Token);
            _dupGroups = groups;
            Invoke(() =>
            {
                progressBar.Value = 10000;
                lblProgress.Text = "100.00%  扫描完成";
                lblStatus.Text = "✓ 已是最新"; lblStatus.ForeColor = Color.LimeGreen;
                btnRefresh.Enabled = true;
                LoadDataAsync();
                RenderDupGroups();
            });
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { Invoke(() => lblProgress.Text = $"错误: {ex.Message}"); }
        finally { _dedup.Progress -= OnScanProgress; _scanRunning = false; }
    }

    private void OnScanProgress(string stage, string msg)
    {
        Invoke(() =>
        {
            var pct = CalcPct(stage, msg);
            progressBar.Value = (int)(pct * 100);
            lblProgress.Text = $"{pct:F2}%  {msg}";
        });
    }

    private double CalcPct(string stage, string msg)
    {
        bool fast = !_fullHashEnabled;
        if (stage == "pass1_done") return fast ? 35.0 : 25.0;
        if (stage == "pass2_done") return fast ? 100.0 : 70.0;
        if (stage == "pass3_done") return 100.0;
        var m = System.Text.RegularExpressions.Regex.Match(msg, @"(\d+)\s*/\s*(\d+)");
        if (!m.Success) return 5.0;
        double cur = int.Parse(m.Groups[1].Value), tot = int.Parse(m.Groups[2].Value);
        if (tot == 0) return 50.0;
        if (msg.Contains("quick")) return 35.0 + cur / tot * (fast ? 65.0 : 45.0);
        if (msg.Contains("full")) return 70.0 + cur / tot * 30.0;
        return cur / tot * (fast ? 35.0 : 25.0);
    }

    private List<string> GetSelectedDrives()
    {
        var drives = new List<string>();
        foreach (var item in checkedListBoxDrives.CheckedItems)
            drives.Add(item.ToString()! + ":\\");
        return drives;
    }

    private void BtnRefresh_Click(object sender, EventArgs e)
    {
        if (MessageBox.Show("全量刷新将重新扫描所有文件。确定？", "确认", MessageBoxButtons.YesNo) == DialogResult.Yes)
            StartScan(force: true);
    }

    // ═══════════════════════════════════════════════
    //  Data Loading
    // ═══════════════════════════════════════════════

    private async void LoadDataAsync()
    {
        await Task.Run(() =>
        {
            try
            {
                var stats = _db!.GetStats();
                Invoke(() => statusLabel.Text =
                    $"已索引 {stats.TotalFiles:N0} 个文件  ·  {stats.DuplicateGroups:N0} 组重复  ·  可释放 {Reporter.FormatSize(stats.WastedBytes)}");

                var rows = _db.SearchFiles("", 200);
                _searchTable = ToSearchTable(rows);
                Invoke(() => { dgvSearch.DataSource = _searchTable; });
            }
            catch { }
        });
    }

    private void RenderDupGroups()
    {
        _dupTable = new DataTable();
        _dupTable.Columns.Add("chk", typeof(bool));
        _dupTable.Columns.Add("#", typeof(int));
        _dupTable.Columns.Add("大小", typeof(string));
        _dupTable.Columns.Add("数量", typeof(string));
        _dupTable.Columns.Add("浪费", typeof(string));
        _dupTable.Columns.Add("保留文件", typeof(string));

        for (int i = 0; i < _dupGroups.Count; i++)
        {
            var g = _dupGroups[i];
            var wasted = (g.Files.Count - 1) * g.Size;
            var keep = g.Files[0].path;
            _dupTable.Rows.Add(false, i + 1, Reporter.FormatSize(g.Size),
                $"{g.Files.Count}×", Reporter.FormatSize(wasted),
                keep.Length > 100 ? "..." + keep[^97..] : keep);
        }
        dgvDups.DataSource = _dupTable;
        dgvDups.Columns["保留文件"].AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;
    }

    private static DataTable ToSearchTable(List<SearchResult> rows)
    {
        var dt = new DataTable();
        dt.Columns.Add("路径", typeof(string));
        dt.Columns.Add("大小", typeof(string));
        dt.Columns.Add("重复", typeof(string));
        foreach (var r in rows)
            dt.Rows.Add(r.Path, Reporter.FormatSize(r.Size), r.DupCount > 0 ? $"{r.DupCount}个重复" : "唯一");
        return dt;
    }

    // ═══════════════════════════════════════════════
    //  Search
    // ═══════════════════════════════════════════════

    private void TxtSearch_TextChanged(object sender, EventArgs e) => DoSearch();

    private async void DoSearch()
    {
        var q = txtSearch.Text.Trim();
        var rows = q.Length < 2
            ? _db!.SearchFiles("", 200)
            : _db!.SearchFiles(q, 200);
        _searchTable = ToSearchTable(rows);
        Invoke(() => { dgvSearch.DataSource = _searchTable; });
    }

    // ═══════════════════════════════════════════════
    //  Delete
    // ═══════════════════════════════════════════════

    private void DeleteFile(string fp)
    {
        if (MessageBox.Show($"确认删除此文件？\n\n{fp}", "确认", MessageBoxButtons.YesNo) != DialogResult.Yes) return;
        try
        {
            if (File.Exists(fp)) FileSystem.DeleteFile(fp, UIOption.OnlyErrorDialogs, RecycleOption.SendToRecycleBin);
            _db!.UpsertFile(fp, 0, 0, "missing");
            LoadDataAsync();
        }
        catch (Exception ex) { MessageBox.Show($"删除失败: {ex.Message}"); }
    }

    private void DgvDups_CellContentClick(object sender, DataGridViewCellEventArgs e)
    {
        if (e.RowIndex < 0) return;
        if (e.ColumnIndex == 0) // checkbox
        {
            dgvDups.CommitEdit(DataGridViewDataErrorContexts.Commit);
            return;
        }

        var g = _dupGroups[e.RowIndex];
        ShowDetailPanel(g);
    }

    private void ShowDetailPanel(DupGroup g)
    {
        panelDetail.Visible = true;
        var dt = new DataTable();
        dt.Columns.Add("chk", typeof(bool));
        dt.Columns.Add("路径", typeof(string));
        dt.Columns.Add("大小", typeof(string));
        dt.Columns.Add("修改时间", typeof(string));
        for (int i = 0; i < g.Files.Count; i++)
        {
            var f = g.Files[i];
            dt.Rows.Add(i != 0, f.path, Reporter.FormatSize(f.size),
                DateTime.FromFileTimeUtc(f.mtime / 100).ToString("yyyy-MM-dd HH:mm"));
        }
        dgvDetail.DataSource = dt;
        dgvDetail.Columns["路径"].AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;
        _currentDetailGroup = g;
    }

    private DupGroup? _currentDetailGroup;

    private void BtnDetailDelete_Click(object sender, EventArgs e)
    {
        if (_currentDetailGroup == null) return;
        var checkedPaths = new List<string>();
        foreach (DataGridViewRow row in dgvDetail.Rows)
            if ((bool)row.Cells[0].Value == true)
                checkedPaths.Add((string)row.Cells[1].Value);

        if (checkedPaths.Count == 0) { MessageBox.Show("请勾选要删除的文件"); return; }
        if (checkedPaths.Count == _currentDetailGroup.Files.Count) { MessageBox.Show("至少保留一个文件"); return; }

        if (MessageBox.Show($"将删除 {checkedPaths.Count} 个文件\n\n{string.Join("\n", checkedPaths.Take(5))}",
            "确认删除", MessageBoxButtons.YesNo) != DialogResult.Yes) return;

        foreach (var fp in checkedPaths) DeleteFile(fp);
        panelDetail.Visible = false;
    }

    // ═══════════════════════════════════════════════
    //  Context Menu
    // ═══════════════════════════════════════════════

    private void DgvDups_CellMouseClick(object sender, DataGridViewCellMouseEventArgs e)
    {
        if (e.Button == MouseButtons.Right && e.RowIndex >= 0)
        {
            dgvDups.CurrentCell = dgvDups.Rows[e.RowIndex].Cells[1];
            var g = _dupGroups[e.RowIndex];
            var menu = new ContextMenuStrip();
            menu.Items.Add("📂 打开文件位置", null, (s, ev) => RevealInExplorer(g.Files[0].path));
            menu.Items.Add("📋 复制路径", null, (s, ev) => Clipboard.SetText(g.Files[0].path));
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("🗑 删除此文件", null, (s, ev) => DeleteFile(g.Files[0].path));
            menu.Show(dgvDups, e.Location);
        }
    }

    // ═══════════════════════════════════════════════
    //  Batch Delete
    // ═══════════════════════════════════════════════

    private void BtnBatchToggle_Click(object sender, EventArgs e)
    {
        bool allChecked = true;
        foreach (DataGridViewRow row in dgvDups.Rows)
            if (!(bool)row.Cells[0].Value) { allChecked = false; break; }
        foreach (DataGridViewRow row in dgvDups.Rows)
            row.Cells[0].Value = !allChecked;
        UpdateBatchInfo();
    }

    private void UpdateBatchInfo()
    {
        var checked_ = 0; long wasted = 0; var files = 0;
        foreach (DataGridViewRow row in dgvDups.Rows)
        {
            if (row.Cells[0].Value is bool b && b)
            {
                var g = _dupGroups[row.Index];
                checked_++;
                wasted += (g.Files.Count - 1) * g.Size;
                files += g.Files.Count - 1;
            }
        }
        lblBatchInfo.Text = checked_ > 0
            ? $"已选 {checked_} 组 | 可删除 {files} 个文件 | 释放 {Reporter.FormatSize(wasted)}"
            : "";
        btnBatchDelete.Enabled = checked_ > 0;
    }

    private void BtnBatchDelete_Click(object sender, EventArgs e)
    {
        var toDelete = new List<string>();
        foreach (DataGridViewRow row in dgvDups.Rows)
        {
            if (row.Cells[0].Value is bool b && b)
            {
                var g = _dupGroups[row.Index];
                toDelete.AddRange(g.Files.Skip(1).Select(f => f.path));
            }
        }
        if (toDelete.Count == 0) return;
        if (MessageBox.Show($"将删除 {toDelete.Count} 个文件\n\n{string.Join("\n", toDelete.Take(5))}",
            "确认", MessageBoxButtons.YesNo) != DialogResult.Yes) return;

        foreach (var fp in toDelete) DeleteFile(fp);
    }

    /// <summary>Sync checkboxes when shift-click or drag selects multiple rows.</summary>
    private void DgvDups_SelectionChanged(object? sender, EventArgs e)
    {
        bool shiftHeld = (ModifierKeys & Keys.Shift) != 0;
        bool mouseDown = (MouseButtons & MouseButtons.Left) != 0;
        if (!shiftHeld && !mouseDown) return;

        var selRows = dgvDups.SelectedRows.Cast<DataGridViewRow>().Select(r => r.Index).ToHashSet();
        if (selRows.Count <= 1) return;

        // Determine direction from the first selected row
        int firstRow = selRows.Min();
        bool makeChecked = !(bool)dgvDups.Rows[firstRow].Cells[0].Value!;

        foreach (int r in selRows)
            dgvDups.Rows[r].Cells[0].Value = makeChecked;

        UpdateBatchInfo();
    }

    private void DgvDups_CellValueChanged(object? sender, DataGridViewCellEventArgs e)
    {
        if (e.ColumnIndex == 0) UpdateBatchInfo();
    }

    // ═══════════════════════════════════════════════
    //  Drive Capacity
    // ═══════════════════════════════════════════════

    private void TimerCapacity_Tick(object sender, EventArgs e) => UpdateDriveCapacity();
    private void CheckedListBoxDrives_ItemCheck(object sender, ItemCheckEventArgs e) =>
        BeginInvoke(UpdateDriveCapacity);

    private void UpdateDriveCapacity()
    {
        long total = 0, free = 0; int count = 0;
        foreach (var item in checkedListBoxDrives.CheckedItems)
        {
            var drive = new DriveInfo(item.ToString()! + ":\\");
            total += drive.TotalSize; free += drive.AvailableFreeSpace; count++;
        }
        lblCapacity.Text = count > 0
            ? $"已选 {count} 盘 | 总 {Reporter.FormatSize(total, 4)} | 可用 {Reporter.FormatSize(free, 4)}"
            : "";
    }

    // ═══════════════════════════════════════════════
    //  Folder Compare Tab
    // ═══════════════════════════════════════════════

    private async void BtnCmpStart_Click(object sender, EventArgs e)
    {
        var fa = txtCmpA.Text.Trim(); var fb = txtCmpB.Text.Trim();
        if (!Directory.Exists(fa) || !Directory.Exists(fb)) { MessageBox.Show("请选择有效的文件夹"); return; }

        btnCmpStart.Enabled = false; lblCmpStatus.Text = "● 比对中...";

        var exts = _activeExts.Count > 0 ? _activeExts : null;
        await Task.Run(() =>
        {
            var hashesA = new Dictionary<byte[], List<(string path, long size)>>(new ByteArrayComparer());
            foreach (var entry in Scanner.WalkFiles([fa], exts))
            {
                var qh = Hasher.QuickHash(entry.Path);
                if (qh == null) continue;
                if (!hashesA.ContainsKey(qh)) hashesA[qh] = [];
                hashesA[qh].Add((entry.Path, entry.Size));
            }
            var hashesB = new Dictionary<byte[], List<(string, long)>>(new ByteArrayComparer());
            foreach (var entry in Scanner.WalkFiles([fb], exts))
            {
                var qh = Hasher.QuickHash(entry.Path);
                if (qh == null) continue;
                if (!hashesB.ContainsKey(qh)) hashesB[qh] = [];
                hashesB[qh].Add((entry.Path, entry.Size));
            }

            var common = hashesA.Keys.Intersect(hashesB.Keys, new ByteArrayComparer());
            _cmpTable = new DataTable();
            _cmpTable.Columns.Add("大小", typeof(string));
            _cmpTable.Columns.Add("路径(A)", typeof(string));
            _cmpTable.Columns.Add("路径(B)", typeof(string));
            foreach (var qh in common)
                foreach (var (pa, sz) in hashesA[qh])
                    foreach (var (pb, _) in hashesB[qh])
                        _cmpTable.Rows.Add(Reporter.FormatSize(sz), pa, pb);

            Invoke(() =>
            {
                dgvCmp.DataSource = _cmpTable;
                dgvCmp.Columns["路径(B)"].AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;
                lblCmpStatus.Text = $"✓ {_cmpTable.Rows.Count} 对重复";
            });
        });
        btnCmpStart.Enabled = true;
    }

    // ═══════════════════════════════════════════════
    //  Helpers
    // ═══════════════════════════════════════════════

    private static void RevealInExplorer(string path)
    {
        if (File.Exists(path) || Directory.Exists(path))
            System.Diagnostics.Process.Start("explorer.exe", $"/select,\"{path}\"");
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        _scanCts?.Cancel();
        _db?.Dispose();
        base.OnFormClosing(e);
    }
}

internal class ByteArrayComparer : IEqualityComparer<byte[]>
{
    public bool Equals(byte[]? x, byte[]? y) => x != null && y != null && x.AsSpan().SequenceEqual(y);
    public int GetHashCode(byte[] obj) => obj[0] ^ obj.Length;
}
