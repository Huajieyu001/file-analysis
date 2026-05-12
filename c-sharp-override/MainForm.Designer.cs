namespace CleanDup;

partial class MainForm
{
    private System.ComponentModel.IContainer components = null!;
    private TabControl tabControl;
    private TabPage tabDups, tabSearch, tabCompare, tabLocal, tabEmpty;

    // Common controls
    private Label lblStatus, lblProgress, lblCapacity, statusLabel;
    private TextBox txtSearch;
    private CheckedListBox checkedListBoxDrives;
    private Button btnRefresh, btnSettings;
    private ProgressBar progressBar;

    // Dups tab
    private DataGridView dgvDups, dgvDetail;
    private Panel panelDetail;
    private Button btnBatchToggle, btnBatchDelete, btnDetailDelete, btnDetailToggle;
    private Label lblBatchInfo;

    // Search tab
    private DataGridView dgvSearch;

    // Compare tab
    private TextBox txtCmpA, txtCmpB;
    private Button btnCmpStart, btnCmpBrowseA, btnCmpBrowseB;
    private DataGridView dgvCmp;
    private Label lblCmpStatus;

    // Local check tab
    private TextBox txtLocalFolder;
    private Button btnLocalCheck, btnLocalBrowse, btnLocalDel, btnLocalKeep;
    private DataGridView dgvLocal;
    private Label lblLocalStatus;

    // Empty cleanup tab
    private Button btnEmptyScan, btnEmptyDel, btnEmptyToggle;
    private DataGridView dgvEmpty;
    private Label lblEmptyStatus;

    // Timer
    private System.Windows.Forms.Timer timerCapacity;

    protected override void Dispose(bool disposing)
    {
        if (disposing && components != null) components.Dispose();
        base.Dispose(disposing);
    }

    private void InitializeComponent()
    {
        this.Text = "CleanDup";
        this.Size = new Size(1180, 760);
        this.MinimumSize = new Size(900, 550);
        this.StartPosition = FormStartPosition.CenterScreen;
        this.BackColor = Color.FromArgb(0x0F, 0x14, 0x19);
        this.ForeColor = Color.FromArgb(0xC0, 0xC5, 0xD4);
        this.Load += MainForm_Load;
        this.FormClosing += (s, e) => { components?.Dispose(); };

        // Set icon
        var iconPath = Path.Combine(AppContext.BaseDirectory, "app_icon.ico");
        if (File.Exists(iconPath)) this.Icon = new Icon(iconPath);

        var mainPanel = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(12, 10, 12, 8), RowCount = 8, ColumnCount = 1 };
        mainPanel.RowStyles.Add(new RowStyle(SizeType.AutoSize)); // search
        mainPanel.RowStyles.Add(new RowStyle(SizeType.AutoSize)); // drives
        mainPanel.RowStyles.Add(new RowStyle(SizeType.AutoSize)); // buttons
        mainPanel.RowStyles.Add(new RowStyle(SizeType.AutoSize)); // progress bar
        mainPanel.RowStyles.Add(new RowStyle(SizeType.AutoSize)); // progress text
        mainPanel.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); // tabs
        mainPanel.RowStyles.Add(new RowStyle(SizeType.AutoSize)); // capacity
        mainPanel.RowStyles.Add(new RowStyle(SizeType.AutoSize)); // status bar
        this.Controls.Add(mainPanel);

        // ── Search bar ──
        var searchRow = new FlowLayoutPanel { AutoSize = true, Anchor = AnchorStyles.Left | AnchorStyles.Right, WrapContents = false };
        searchRow.Controls.Add(new Label { Text = "🔍", ForeColor = Color.Gray });
        txtSearch = new TextBox { Width = 400, BackColor = Color.FromArgb(0x1A, 0x1D, 0x29), ForeColor = Color.FromArgb(0xC0, 0xC5, 0xD4), BorderStyle = BorderStyle.None };
        txtSearch.TextChanged += TxtSearch_TextChanged;
        searchRow.Controls.Add(txtSearch);
        mainPanel.Controls.Add(searchRow, 0, 0);

        // ── Drives ──
        var driveRow = new FlowLayoutPanel { AutoSize = true, WrapContents = true };
        checkedListBoxDrives = new CheckedListBox { BackColor = Color.FromArgb(0x1A, 0x1D, 0x29), ForeColor = Color.FromArgb(0xC0, 0xC5, 0xD4), BorderStyle = BorderStyle.None, MultiColumn = true, Height = 28 };
        foreach (var d in DriveInfo.GetDrives()) checkedListBoxDrives.Items.Add(d.Name.TrimEnd('\\'), d.Name != @"C:\");
        checkedListBoxDrives.ItemCheck += CheckedListBoxDrives_ItemCheck;
        driveRow.Controls.Add(new Label { Text = "盘符:", ForeColor = Color.Gray });
        driveRow.Controls.Add(checkedListBoxDrives);
        mainPanel.Controls.Add(driveRow, 0, 1);

        // ── Button row ──
        var btnRow = new FlowLayoutPanel { AutoSize = true };
        lblStatus = new Label { Text = "● 准备中...", ForeColor = Color.Orange, Font = new Font("Segoe UI", 10, FontStyle.Bold) };
        btnRow.Controls.Add(lblStatus);
        btnRefresh = new Button { Text = "⟳ 全量刷新", BackColor = Color.FromArgb(0x1E, 0x22, 0x40), ForeColor = Color.FromArgb(0x8B, 0xE9, 0xFD), FlatStyle = FlatStyle.Flat };
        btnRefresh.Click += BtnRefresh_Click;
        btnRow.Controls.Add(btnRefresh);
        btnSettings = new Button { Text = "设置", BackColor = Color.FromArgb(0x1E, 0x22, 0x40), ForeColor = Color.FromArgb(0x8B, 0xE9, 0xFD), FlatStyle = FlatStyle.Flat };
        btnSettings.Click += BtnSettings_Click;
        btnRow.Controls.Add(btnSettings);
        mainPanel.Controls.Add(btnRow, 0, 2);

        // ── Progress ──
        progressBar = new ProgressBar { Maximum = 10000, Height = 4, Anchor = AnchorStyles.Left | AnchorStyles.Right, BackColor = Color.FromArgb(0x1A, 0x1D, 0x29) };
        mainPanel.Controls.Add(progressBar, 0, 3);
        lblProgress = new Label { Text = "", ForeColor = Color.Gray, Font = new Font("Segoe UI", 9) };
        mainPanel.Controls.Add(lblProgress, 0, 4);

        // ── Tabs ──
        tabControl = new TabControl { Dock = DockStyle.Fill, BackColor = Color.FromArgb(0x1A, 0x1D, 0x29) };
        tabDups = new TabPage("重复组"); tabSearch = new TabPage("搜索结果");
        tabCompare = new TabPage("文件夹比对"); tabLocal = new TabPage("本地查重");
        tabEmpty = new TabPage("清理空文件");
        tabControl.TabPages.AddRange([tabDups, tabSearch, tabCompare, tabLocal, tabEmpty]);
        mainPanel.Controls.Add(tabControl, 0, 5);

        // -- Tab: Dups --
        BuildDupsTab();
        // -- Tab: Search --
        BuildSearchTab();
        // -- Tab: Compare --
        BuildCompareTab();
        // -- Tab: Local --
        BuildLocalTab();
        // -- Tab: Empty --
        BuildEmptyTab();

        // ── Drive capacity ──
        lblCapacity = new Label { Text = "", ForeColor = Color.FromArgb(0x8B, 0xE9, 0xFD), Anchor = AnchorStyles.Right };
        mainPanel.Controls.Add(lblCapacity, 0, 6);

        // ── Status bar ──
        statusLabel = new Label { Text = "", ForeColor = Color.DarkGray, Dock = DockStyle.Bottom, Font = new Font("Segoe UI", 9) };
        mainPanel.Controls.Add(statusLabel, 0, 7);

        // ── Timer ──
        timerCapacity = new System.Windows.Forms.Timer { Interval = 30000 };
        timerCapacity.Tick += TimerCapacity_Tick;
        timerCapacity.Start();
        UpdateDriveCapacity();
    }

    private void BuildDupsTab()
    {
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 3, ColumnCount = 1 };
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 70));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 30));

        // Batch bar
        var batchBar = new FlowLayoutPanel { AutoSize = true };
        btnBatchToggle = new Button { Text = "全选", BackColor = Color.FromArgb(0x1E, 0x22, 0x40), ForeColor = Color.FromArgb(0x8B, 0xE9, 0xFD), FlatStyle = FlatStyle.Flat };
        btnBatchToggle.Click += BtnBatchToggle_Click;
        batchBar.Controls.Add(btnBatchToggle);
        btnBatchDelete = new Button { Text = "🗑 批量清理勾选的组", BackColor = Color.Firebrick, ForeColor = Color.White, FlatStyle = FlatStyle.Flat, Enabled = false };
        btnBatchDelete.Click += BtnBatchDelete_Click;
        batchBar.Controls.Add(btnBatchDelete);
        lblBatchInfo = new Label { Text = "", ForeColor = Color.Gray };
        batchBar.Controls.Add(lblBatchInfo);
        layout.Controls.Add(batchBar, 0, 0);

        // Group list
        dgvDups = new DataGridView { Dock = DockStyle.Fill, BackgroundColor = Color.FromArgb(0x1A, 0x1D, 0x29),
            AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.None, AllowUserToAddRows = false,
            AllowUserToDeleteRows = false, ReadOnly = false, SelectionMode = DataGridViewSelectionMode.FullRowSelect };
        dgvDups.CellContentClick += DgvDups_CellContentClick;
        dgvDups.CellMouseClick += DgvDups_CellMouseClick;
        dgvDups.CellValueChanged += DgvDups_CellValueChanged;
        dgvDups.SelectionChanged += DgvDups_SelectionChanged;
        layout.Controls.Add(dgvDups, 0, 1);

        // Detail panel
        panelDetail = new Panel { Dock = DockStyle.Fill, Visible = false };
        var detailLayout = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 2, ColumnCount = 1 };
        detailLayout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        detailLayout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        var detailBtnRow = new FlowLayoutPanel { AutoSize = true };
        btnDetailToggle = new Button { Text = "全选", BackColor = Color.FromArgb(0x1E, 0x22, 0x40), ForeColor = Color.FromArgb(0x8B, 0xE9, 0xFD), FlatStyle = FlatStyle.Flat };
        btnDetailToggle.Click += (s, e) => { bool all = true; foreach (DataGridViewRow r in dgvDetail.Rows) if (!(bool)r.Cells[0].Value) { all = false; break; } foreach (DataGridViewRow r in dgvDetail.Rows) r.Cells[0].Value = !all; };
        detailBtnRow.Controls.Add(btnDetailToggle);
        btnDetailDelete = new Button { Text = "🗑 删除勾选的文件", BackColor = Color.Firebrick, ForeColor = Color.White, FlatStyle = FlatStyle.Flat };
        btnDetailDelete.Click += BtnDetailDelete_Click;
        detailBtnRow.Controls.Add(btnDetailDelete);
        detailLayout.Controls.Add(detailBtnRow, 0, 0);
        dgvDetail = new DataGridView { Dock = DockStyle.Fill, BackgroundColor = Color.FromArgb(0x1A, 0x1D, 0x29),
            AllowUserToAddRows = false, AllowUserToDeleteRows = false, SelectionMode = DataGridViewSelectionMode.FullRowSelect };
        detailLayout.Controls.Add(dgvDetail, 0, 1);
        panelDetail.Controls.Add(detailLayout);
        layout.Controls.Add(panelDetail, 0, 2);

        tabDups.Controls.Add(layout);
    }

    private void BuildSearchTab()
    {
        dgvSearch = new DataGridView { Dock = DockStyle.Fill, BackgroundColor = Color.FromArgb(0x1A, 0x1D, 0x29),
            AllowUserToAddRows = false, AllowUserToDeleteRows = false, ReadOnly = true,
            SelectionMode = DataGridViewSelectionMode.FullRowSelect };
        dgvSearch.CellMouseDoubleClick += (s, e) => { if (e.RowIndex >= 0) RevealInExplorer(dgvSearch.Rows[e.RowIndex].Cells[0].Value?.ToString()!); };
        tabSearch.Controls.Add(dgvSearch);
    }

    private void BuildCompareTab()
    {
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 3, ColumnCount = 1 };
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));

        var row1 = new FlowLayoutPanel { AutoSize = true };
        row1.Controls.Add(new Label { Text = "文件夹 A:" }); txtCmpA = new TextBox { Width = 300, BackColor = Color.FromArgb(0x1A, 0x1D, 0x29), ForeColor = Color.White, BorderStyle = BorderStyle.FixedSingle }; row1.Controls.Add(txtCmpA);
        btnCmpBrowseA = new Button { Text = "浏览...", FlatStyle = FlatStyle.Flat }; btnCmpBrowseA.Click += (s, e) => { using var dlg = new FolderBrowserDialog(); if (dlg.ShowDialog() == DialogResult.OK) txtCmpA.Text = dlg.SelectedPath; }; row1.Controls.Add(btnCmpBrowseA);
        row1.Controls.Add(new Label { Text = "  文件夹 B:" }); txtCmpB = new TextBox { Width = 300, BackColor = Color.FromArgb(0x1A, 0x1D, 0x29), ForeColor = Color.White, BorderStyle = BorderStyle.FixedSingle }; row1.Controls.Add(txtCmpB);
        btnCmpBrowseB = new Button { Text = "浏览...", FlatStyle = FlatStyle.Flat }; btnCmpBrowseB.Click += (s, e) => { using var dlg = new FolderBrowserDialog(); if (dlg.ShowDialog() == DialogResult.OK) txtCmpB.Text = dlg.SelectedPath; }; row1.Controls.Add(btnCmpBrowseB);
        layout.Controls.Add(row1, 0, 0);

        var row2 = new FlowLayoutPanel { AutoSize = true };
        btnCmpStart = new Button { Text = "🔍 开始比对", BackColor = Color.FromArgb(0x1E, 0x22, 0x40), ForeColor = Color.FromArgb(0x8B, 0xE9, 0xFD), FlatStyle = FlatStyle.Flat };
        btnCmpStart.Click += BtnCmpStart_Click;
        row2.Controls.Add(btnCmpStart);
        lblCmpStatus = new Label { Text = "", ForeColor = Color.Gray }; row2.Controls.Add(lblCmpStatus);
        layout.Controls.Add(row2, 0, 1);

        dgvCmp = new DataGridView { Dock = DockStyle.Fill, BackgroundColor = Color.FromArgb(0x1A, 0x1D, 0x29),
            AllowUserToAddRows = false, AllowUserToDeleteRows = false, ReadOnly = true, SelectionMode = DataGridViewSelectionMode.FullRowSelect };
        layout.Controls.Add(dgvCmp, 0, 2);
        tabCompare.Controls.Add(layout);
    }

    private void BuildLocalTab()
    {
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 2, ColumnCount = 1 };
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));

        var row1 = new FlowLayoutPanel { AutoSize = true };
        row1.Controls.Add(new Label { Text = "文件夹:" }); txtLocalFolder = new TextBox { Width = 350, BackColor = Color.FromArgb(0x1A, 0x1D, 0x29), ForeColor = Color.White, BorderStyle = BorderStyle.FixedSingle }; row1.Controls.Add(txtLocalFolder);
        btnLocalBrowse = new Button { Text = "浏览...", FlatStyle = FlatStyle.Flat }; btnLocalBrowse.Click += (s, e) => { using var dlg = new FolderBrowserDialog(); if (dlg.ShowDialog() == DialogResult.OK) txtLocalFolder.Text = dlg.SelectedPath; }; row1.Controls.Add(btnLocalBrowse);
        btnLocalCheck = new Button { Text = "🔍 查询重复", BackColor = Color.FromArgb(0x1E, 0x22, 0x40), ForeColor = Color.FromArgb(0x8B, 0xE9, 0xFD), FlatStyle = FlatStyle.Flat }; row1.Controls.Add(btnLocalCheck);
        lblLocalStatus = new Label { Text = "", ForeColor = Color.Gray }; row1.Controls.Add(lblLocalStatus);
        btnLocalDel = new Button { Text = "🗑 删除本文件夹", BackColor = Color.Firebrick, ForeColor = Color.White, FlatStyle = FlatStyle.Flat, Enabled = false }; row1.Controls.Add(btnLocalDel);
        btnLocalKeep = new Button { Text = "📌 保留本文件夹", BackColor = Color.DarkOrange, ForeColor = Color.Black, FlatStyle = FlatStyle.Flat, Enabled = false }; row1.Controls.Add(btnLocalKeep);
        layout.Controls.Add(row1, 0, 0);

        dgvLocal = new DataGridView { Dock = DockStyle.Fill, BackgroundColor = Color.FromArgb(0x1A, 0x1D, 0x29),
            AllowUserToAddRows = false, AllowUserToDeleteRows = false, ReadOnly = true, SelectionMode = DataGridViewSelectionMode.FullRowSelect };
        layout.Controls.Add(dgvLocal, 0, 1);
        tabLocal.Controls.Add(layout);
    }

    private void BuildEmptyTab()
    {
        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 2, ColumnCount = 1 };
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));

        var row1 = new FlowLayoutPanel { AutoSize = true };
        btnEmptyScan = new Button { Text = "🔍 扫描空文件/空文件夹", BackColor = Color.FromArgb(0x1E, 0x22, 0x40), ForeColor = Color.FromArgb(0x8B, 0xE9, 0xFD), FlatStyle = FlatStyle.Flat }; row1.Controls.Add(btnEmptyScan);
        lblEmptyStatus = new Label { Text = "", ForeColor = Color.Gray }; row1.Controls.Add(lblEmptyStatus);
        btnEmptyToggle = new Button { Text = "全选", BackColor = Color.FromArgb(0x1E, 0x22, 0x40), ForeColor = Color.FromArgb(0x8B, 0xE9, 0xFD), FlatStyle = FlatStyle.Flat }; row1.Controls.Add(btnEmptyToggle);
        btnEmptyDel = new Button { Text = "🗑 删除选中的项目", BackColor = Color.Firebrick, ForeColor = Color.White, FlatStyle = FlatStyle.Flat, Enabled = false }; row1.Controls.Add(btnEmptyDel);
        layout.Controls.Add(row1, 0, 0);

        dgvEmpty = new DataGridView { Dock = DockStyle.Fill, BackgroundColor = Color.FromArgb(0x1A, 0x1D, 0x29),
            AllowUserToAddRows = false, AllowUserToDeleteRows = false, SelectionMode = DataGridViewSelectionMode.FullRowSelect };
        layout.Controls.Add(dgvEmpty, 0, 1);
        tabEmpty.Controls.Add(layout);
    }
}
