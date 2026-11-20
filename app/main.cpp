#include <QtWidgets>
#include <QCryptographicHash>
#include <QDesktopServices>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QSaveFile>
#include <QStandardPaths>
#include <QTemporaryFile>
#include <memory>

namespace {
QString quote(QString value)
{
    return "'" + value.replace("'", "'\\''") + "'";
}

QString adjacent(const QString &local, const QString &installed)
{
    QDir base(QCoreApplication::applicationDirPath());
    const auto candidate = base.absoluteFilePath(local);
    return QFileInfo::exists(candidate) ? candidate : base.absoluteFilePath(installed);
}

QLabel *hint(const QString &text)
{
    auto *label = new QLabel(text);
    label->setWordWrap(true);
    label->setProperty("muted", true);
    return label;
}

QPushButton *button(const QString &text, QLayout *layout)
{
    auto *widget = new QPushButton(text);
    layout->addWidget(widget);
    return widget;
}

QTableWidget *table(const QStringList &columns)
{
    auto *widget = new QTableWidget(0, columns.size());
    widget->setHorizontalHeaderLabels(columns);
    widget->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);
    widget->verticalHeader()->setDefaultSectionSize(32);
    widget->setSelectionBehavior(QAbstractItemView::SelectRows);
    widget->setAlternatingRowColors(true);
    return widget;
}

void addRow(QTableWidget *table, const QStringList &values)
{
    const int row = table->rowCount();
    table->insertRow(row);
    for (int column = 0; column < table->columnCount(); ++column)
        table->setItem(row, column, new QTableWidgetItem(values.value(column)));
}

QString cell(QTableWidget *table, int row, int column)
{
    const auto *item = table->item(row, column);
    return item ? item->text().trimmed() : QString{};
}
}

class Studio : public QMainWindow {
public:
    Studio()
    {
        setWindowTitle("Robot Studio");
        resize(960, 680);
        setMinimumSize(760, 620);
        auto *root = new QWidget;
        auto *layout = new QVBoxLayout(root);
        layout->setContentsMargins(20, 16, 20, 0);
        auto *header = new QHBoxLayout;
        auto *title = new QLabel("Robot Studio");
        title->setObjectName("title");
        connected = new QLabel("USB 리커버리 대기");
        connected->setProperty("muted", true);
        header->addWidget(title);
        header->addStretch();
        header->addWidget(connected);
        layout->addLayout(header);
        auto *splitter = new QSplitter(Qt::Vertical);
        tabs = new QTabWidget;
        splitter->addWidget(tabs);
        log = new QPlainTextEdit;
        log->setReadOnly(true);
        log->document()->setMaximumBlockCount(1200);
        log->setPlaceholderText("작업 로그");
        log->setMinimumHeight(82);
        log->setFont(QFontDatabase::systemFont(QFontDatabase::FixedFont));
        splitter->addWidget(log);
        splitter->setStretchFactor(0, 5);
        splitter->setStretchFactor(1, 1);
        splitter->setSizes({470, 145});
        layout->addWidget(splitter, 1);
        auto *footer = new QHBoxLayout;
        state = new QLabel("준비");
        bar = new QProgressBar;
        bar->setMaximumWidth(270);
        bar->hide();
        stop = button("다운로드 중지", footer);
        stop->hide();
        connect(stop, &QPushButton::clicked, this, [this] { process.terminate(); });
        footer->insertWidget(0, state, 1);
        footer->insertWidget(1, bar);
        layout->addLayout(footer);
        setCentralWidget(root);

        if (loadCatalog()) {
            buildFlash();
            buildRobot();
            buildVerify();
            buildManuals();
            connect(&timer, &QTimer::timeout, this, [this] { scan(); });
            timer.start(1500);
            scan();
        } else {
            auto *page = new QWidget;
            auto *pageLayout = new QVBoxLayout(page);
            pageLayout->addWidget(hint("설치 구성 요소를 읽을 수 없습니다. 패키지를 다시 설치하세요."));
            tabs->addTab(page, "설치");
        }

        process.setProcessChannelMode(QProcess::MergedChannels);
        connect(&process, &QProcess::readyReadStandardOutput, this, [this] {
            consume(process.readAllStandardOutput());
        });
        connect(&process, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this,
                [this](int code, QProcess::ExitStatus status) {
            consume(process.readAllStandardOutput());
            if (!pending.isEmpty())
                consume("\n");
            finish(code == 0 && status == QProcess::NormalExit);
        });
        connect(&process, &QProcess::errorOccurred, this, [this](QProcess::ProcessError error) {
            if (error == QProcess::FailedToStart) {
                append(process.errorString());
                finish(false);
            }
        });
    }

    bool isReady() const { return !profiles.isEmpty(); }

protected:
    void closeEvent(QCloseEvent *event) override
    {
        if (busy) {
            append(cancellable ? "다운로드 중지 후 창을 닫을 수 있습니다." : "작업이 끝난 뒤 창을 닫을 수 있습니다.");
            event->ignore();
            return;
        }
        event->accept();
    }

private:
    const QString backend = adjacent("backend/wego-jetson-backend/wego-jetson-backend",
                                     "../lib/wego-jetson/backend/wego-jetson-backend");
    const QString docs = adjacent("docs", "../share/doc/wego-jetson");
    const QString dataRoot = QStandardPaths::writableLocation(QStandardPaths::AppLocalDataLocation);
    QJsonArray profiles, robots;
    QJsonObject recovery;
    QString cache, qspi, sessionLog;
    QByteArray pending;
    bool busy = false, cancellable = false;
    QProcess process, scanner;
    QTimer timer;
    QSettings settings;
    std::unique_ptr<QTemporaryFile> payload;
    QTabWidget *tabs = nullptr;
    QComboBox *board = nullptr, *release = nullptr, *mode = nullptr, *robot = nullptr, *softwareBoard = nullptr;
    QLineEdit *login = nullptr;
    QTableWidget *net = nullptr, *topics = nullptr;
    QTreeWidget *manuals = nullptr;
    QLineEdit *ddsInterface = nullptr;
    QSpinBox *duration = nullptr, *ddsDomain = nullptr;
    QLabel *connected = nullptr, *state = nullptr, *boardInfo = nullptr;
    QPlainTextEdit *log = nullptr;
    QProgressBar *bar = nullptr;
    QPushButton *stop = nullptr, *flashButton = nullptr;

    void append(const QString &line) { log->appendPlainText(line); }

    bool loadCatalog()
    {
        QProcess loader;
        loader.start(backend, {"catalog"});
        if (!loader.waitForFinished(3500) || loader.exitCode() != 0) {
            append("설치 도우미를 시작할 수 없습니다. " + loader.errorString());
            return false;
        }
        const auto catalog = QJsonDocument::fromJson(loader.readAllStandardOutput()).object();
        profiles = catalog["profiles"].toArray();
        robots = catalog["robots"].toArray();
        cache = catalog["cache"].toString();
        qspi = catalog["qspi"].toString();
        return !profiles.isEmpty() && !robots.isEmpty() && !cache.isEmpty() && !qspi.isEmpty();
    }

    QVBoxLayout *page(const QString &label)
    {
        auto *widget = new QWidget;
        auto *layout = new QVBoxLayout(widget);
        layout->setContentsMargins(16, 14, 16, 14);
        layout->setSpacing(10);
        tabs->addTab(widget, label);
        return layout;
    }

    QJsonObject profile() const { return release->currentData().toJsonObject(); }

    void buildFlash()
    {
        auto *layout = page("플래싱");
        auto *native = new QGroupBox("Jetson 보드");
        auto *nativeLayout = new QVBoxLayout(native);
        auto *form = new QFormLayout;
        board = new QComboBox;
        release = new QComboBox;
        mode = new QComboBox;
        board->addItem("캐리어보드 선택", "");
        for (const auto &value : profiles) {
            auto item = value.toObject();
            if (board->findData(item["board"].toString()) == -1)
                board->addItem(item["label"].toString(), item["board"].toString());
        }
        form->addRow("캐리어보드", board);
        form->addRow("JetPack", release);
        form->addRow("설치 방식", mode);
        nativeLayout->addLayout(form);
        boardInfo = hint("보드에 적힌 모델을 선택하세요. USB에서는 Jetson 모듈을 확인합니다.");
        nativeLayout->addWidget(boardInfo);
        auto *row = new QHBoxLayout;
        auto *download = button("파일 다운로드", row);
        flashButton = button("플래싱 시작", row);
        flashButton->setProperty("primary", true);
        row->addStretch();
        nativeLayout->addLayout(row);
        layout->addWidget(native);
        connect(board, qOverload<int>(&QComboBox::currentIndexChanged), this, [this] { selectBoard(); });
        connect(release, qOverload<int>(&QComboBox::currentIndexChanged), this, [this] { selectRelease(); });
        connect(download, &QPushButton::clicked, this, [this] {
            const auto key = profile()["key"].toString();
            if (key.isEmpty()) { append("캐리어보드와 JetPack을 선택하세요."); return; }
            start("플래싱 파일 다운로드", backend, {"download", key}, true);
        });
        connect(flashButton, &QPushButton::clicked, this, [this] { flash(); });

        auto *other = new QGroupBox("기타 캐리어보드");
        auto *otherLayout = new QVBoxLayout(other);
        otherLayout->addWidget(hint("NVIDIA 정품은 SDK Manager에서 선택합니다. 다른 제조사 보드는 해당 BSP를 사용하세요."));
        auto *actions = new QHBoxLayout;
        connect(button("NVIDIA SDK Manager", actions), &QPushButton::clicked, this, [this] { sdkDialog(); });
        connect(button("제조사 BSP 받기", actions), &QPushButton::clicked, this, [this] { downloadVendor(); });
        connect(button("제조사 설치 도구", actions), &QPushButton::clicked, this, [this] { vendorTool(); });
        actions->addStretch();
        otherLayout->addLayout(actions);
        layout->addWidget(other);
        layout->addStretch();
        selectBoard();
    }

    void selectBoard()
    {
        const QSignalBlocker block(release);
        release->clear();
        const auto selected = board->currentData().toString();
        for (const auto &value : profiles) {
            auto item = value.toObject();
            if (item["board"].toString() == selected)
                release->addItem(item["jetpack"].toString() + "  |  L4T " + item["l4t"].toString(), item);
        }
        selectRelease();
    }

    void selectRelease()
    {
        mode->clear();
        for (const auto &item : profile()["modes"].toArray())
            mode->addItem(item.toString());
        updateBoardStatus();
    }

    void scan()
    {
        if (busy || scanner.state() != QProcess::NotRunning)
            return;
        if (scanConnected == false) {
            scanConnected = true;
            connect(&scanner, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this,
                    [this](int code, QProcess::ExitStatus status) {
                const auto bytes = scanner.readAllStandardOutput();
                const auto error = QString::fromUtf8(scanner.readAllStandardError()).trimmed();
                recovery = code == 0 && status == QProcess::NormalExit
                    ? QJsonDocument::fromJson(bytes).object() : QJsonObject{};
                connected->setText(recovery.isEmpty()
                    ? (error.isEmpty() ? "USB 리커버리 대기" : error)
                    : recovery["model"].toString() + "  |  USB " + recovery["port"].toString());
                updateBoardStatus();
            });
        }
        scanner.start(backend, {"recovery"});
    }

    bool scanConnected = false;

    void updateBoardStatus()
    {
        const auto item = profile();
        const auto id = recovery["pid"].toString();
        const bool match = !id.isEmpty() && item["pids"].toArray().contains(QJsonValue(id));
        flashButton->setEnabled(match);
        if (!match && !item.isEmpty() && !id.isEmpty())
            boardInfo->setText("감지한 모듈은 이 조합에서 지원하지 않습니다.");
        else if (match)
            boardInfo->setText("모듈 확인 완료. 실물 캐리어보드 모델도 확인하세요.");
        else
            boardInfo->setText("보드에 적힌 모델을 선택하세요. USB에서는 Jetson 모듈을 확인합니다.");
    }

    void flash()
    {
        auto item = profile();
        const auto id = recovery["pid"].toString();
        const auto port = recovery["port"].toString();
        if (port.isEmpty() || !item["pids"].toArray().contains(QJsonValue(id))) return;
        const auto selectedMode = mode->currentText();
        if (selectedMode == qspi && id != "7323") { append("QSPI 전용 설치는 NX215B + Orin NX 16GB 조합에서 지원합니다."); return; }
        QString detail = selectedMode == qspi
            ? "복제 SSD의 L4T 버전이 선택한 버전과 같은지 확인하세요."
            : "설치 대상 저장 장치의 데이터가 지워집니다.";
        if (item["board"].toString() == "P4071")
            detail += "\n공식 기본 NVMe 레이아웃은 234 GiB 초과 저장 공간을 요구합니다.";
        const auto summary = item["label"].toString() + "\n" + recovery["model"].toString() + "\n"
            + release->currentText() + "\n" + selectedMode + "\n\n" + detail;
        if (QMessageBox::warning(this, "플래싱 확인", summary, QMessageBox::Ok | QMessageBox::Cancel,
                                 QMessageBox::Cancel) == QMessageBox::Ok)
            terminal("플래싱", {"flash", item["key"].toString(), selectedMode, id, "--usb-port", port});
    }

    void sdkDialog()
    {
        QDialog dialog(this);
        dialog.setWindowTitle("NVIDIA SDK Manager");
        auto *layout = new QVBoxLayout(&dialog);
        layout->addWidget(hint("정품 보드는 SDK Manager에서 선택하세요. 제조사 보드는 해당 업체의 extra config JSON을 사용하세요."));
        auto *file = new QLineEdit;
        file->setPlaceholderText("제조사 extra config (선택)");
        file->setReadOnly(true);
        auto *fileRow = new QHBoxLayout;
        fileRow->addWidget(file);
        connect(button("선택", fileRow), &QPushButton::clicked, &dialog, [this, file] {
            auto path = QFileDialog::getOpenFileName(this, "제조사 extra config", {}, "JSON (*.json)");
            if (!path.isEmpty()) file->setText(path);
        });
        layout->addLayout(fileRow);
        auto *archived = new QCheckBox("이전 JetPack 버전 표시");
        layout->addWidget(archived);
        auto *row = new QHBoxLayout;
        QString operation;
        for (const auto &pair : {qMakePair(QString("다운로드"), QString("downloadonly")),
                                 qMakePair(QString("설치 열기"), QString("install"))}) {
            connect(button(pair.first, row), &QPushButton::clicked, &dialog, [&, pair] {
                operation = pair.second;
                dialog.accept();
            });
        }
        layout->addLayout(row);
        if (dialog.exec() != QDialog::Accepted) return;
        QStringList args{"sdkmanager", operation};
        if (!file->text().isEmpty()) args << "--config" << file->text();
        if (archived->isChecked()) args << "--archived";
        terminal("SDK Manager", args);
    }

    void downloadVendor()
    {
        QDialog dialog(this);
        dialog.setWindowTitle("제조사 BSP 다운로드");
        auto *form = new QFormLayout(&dialog);
        auto *url = new QLineEdit, *sha = new QLineEdit;
        url->setMinimumWidth(425);
        form->addRow("HTTPS 파일 주소", url);
        form->addRow("SHA-256 (선택)", sha);
        auto *buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel);
        form->addRow(buttons);
        connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
        connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
        if (dialog.exec() == QDialog::Accepted)
            start("제조사 BSP 다운로드", backend, {"fetch", url->text().trimmed(), sha->text().trimmed()}, true);
    }

    void vendorTool()
    {
        const auto path = QFileDialog::getOpenFileName(this, "제조사 설치 도구", cache + "/manufacturer", "Shell (*.sh *.bash)");
        if (path.isEmpty()) return;
        if (QMessageBox::warning(this, "제조사 설치 도구", "선택한 제조사 스크립트가 저장 장치를 변경할 수 있습니다.\n" + path,
                                 QMessageBox::Ok | QMessageBox::Cancel, QMessageBox::Cancel) == QMessageBox::Ok)
            terminal("제조사 설치 도구", {"manufacturer", path});
    }

    void buildRobot()
    {
        auto *layout = page("로봇");
        auto *form = new QFormLayout;
        robot = new QComboBox;
        for (const auto &value : robots) {
            const auto item = value.toObject();
            robot->addItem(item["label"].toString(), item);
        }
        login = new QLineEdit("nvidia");
        softwareBoard = new QComboBox;
        softwareBoard->addItem("부팅된 Jetson에서 확인", "auto");
        for (const auto &value : profiles) {
            auto p = value.toObject();
            softwareBoard->addItem(p["label"].toString() + "  |  " + p["jetpack"].toString(), p["key"].toString());
        }
        form->addRow("로봇", robot);
        form->addRow("Jetson 계정", login);
        form->addRow("설치 대상", softwareBoard);
        layout->addLayout(form);
        auto *actions = new QHBoxLayout;
        connect(button("JetPack", actions), &QPushButton::clicked, this, [this] { remote("JetPack 설치", "jetpack"); });
        connect(button("ROS 2 / 로봇 드라이버", actions), &QPushButton::clicked, this, [this] { remote("로봇 소프트웨어 설치", "ros"); });
        connect(button("SLAM / Navigation", actions), &QPushButton::clicked, this, [this] { remote("SLAM / Navigation 설치", "navigation"); });
        connect(button("드라이버 .deb 추가", actions), &QPushButton::clicked, this, [this] {
            auto path = QFileDialog::getOpenFileName(this, "로봇 또는 센서 드라이버", {}, "Debian (*.deb)");
            if (!path.isEmpty() && QMessageBox::question(this, "드라이버 설치", QFileInfo(path).fileName() + " 패키지를 Jetson에 설치할까요?") == QMessageBox::Yes)
                remote("드라이버 설치", "driver", path);
        });
        actions->addStretch();
        layout->addLayout(actions);
        auto *group = new QGroupBox("센서 / 제어기 네트워크");
        auto *grid = new QVBoxLayout(group);
        net = table({"장비 / 인터페이스 이름", "Jetson의 실제 포트", "Jetson IPv4 / CIDR"});
        grid->addWidget(net);
        auto *row = new QHBoxLayout;
        connect(button("행 추가", row), &QPushButton::clicked, this, [this] { addRow(net, {}); });
        connect(button("행 삭제", row), &QPushButton::clicked, this, [this] { if (net->currentRow() >= 0) net->removeRow(net->currentRow()); });
        connect(button("Go2 / MID360 예시", row), &QPushButton::clicked, this, [this] {
            addRow(net, {"go2", "", "192.168.123.99/24"});
            addRow(net, {"mid360", "", "192.168.1.5/24"});
        });
        row->addStretch();
        connect(button("포트 확인", row), &QPushButton::clicked, this, [this] { remote("포트 확인", "ports"); });
        connect(button("설정 저장", row), &QPushButton::clicked, this, [this] {
            QJsonArray entries;
            for (int i = 0; i < net->rowCount(); ++i) {
                auto name = cell(net, i, 0), port = cell(net, i, 1), address = cell(net, i, 2);
                if (name.isEmpty() && port.isEmpty() && address.isEmpty()) continue;
                if (name.isEmpty() || port.isEmpty() || address.isEmpty()) {
                    append(QString("네트워크 %1행의 세 항목을 입력하세요.").arg(i + 1)); return;
                }
                entries.append(QJsonObject{{"name", name}, {"port", port}, {"address", address}});
            }
            if (entries.isEmpty()) { append("저장할 네트워크 행을 추가하세요."); return; }
            if (QMessageBox::question(this, "이더넷 저장", "지정한 유선 포트 설정을 저장합니다. Jetson 재부팅 후 적용할까요?") != QMessageBox::Yes)
                return;
            auto file = jsonFile(QJsonDocument(entries));
            if (!file.isEmpty()) remote("이더넷 설정", "network", file);
        });
        grid->addLayout(row);
        layout->addWidget(group, 1);
    }

    QString jsonFile(const QJsonDocument &data)
    {
        payload = std::make_unique<QTemporaryFile>();
        if (!payload->open() || payload->write(data.toJson()) < 0 || !payload->flush()) {
            append("설정 파일을 만들 수 없습니다.");
            payload.reset();
            return {};
        }
        const auto path = payload->fileName();
        payload->close();
        return path;
    }

    void remote(const QString &label, const QString &action, const QString &file = {})
    {
        QStringList arguments{"postboot", softwareBoard->currentData().toString(), login->text().trimmed(),
                              action, "", "", robot->currentData().toJsonObject()["key"].toString()};
        if (!file.isEmpty()) arguments << "--payload" << file;
        terminal(label, arguments);
    }

    void buildVerify()
    {
        auto *layout = page("검수");
        layout->addWidget(hint("Jetson에서 드라이버를 실행한 다음 상태·LiDAR 토픽을 검수합니다. 각 행의 실제 수신 횟수를 확인합니다."));
        topics = table({"ROS 토픽", "메시지 형식 (선택)", "최소 수신 횟수"});
        layout->addWidget(topics, 1);
        auto *row = new QHBoxLayout;
        connect(button("토픽 추가", row), &QPushButton::clicked, this, [this] { addRow(topics, {"", "", "1"}); });
        connect(button("행 삭제", row), &QPushButton::clicked, this, [this] { if (topics->currentRow() >= 0) topics->removeRow(topics->currentRow()); });
        auto *preset = new QComboBox;
        preset->addItem("장비 토픽 예시");
        preset->addItem("선택한 로봇 상태");
        preset->addItem("LiDAR LaserScan", "/scan");
        preset->addItem("Livox 점군", "/livox/lidar");
        preset->addItem("RealSense 점군", "/camera/camera/depth/color/points");
        row->addWidget(preset);
        connect(preset, qOverload<int>(&QComboBox::activated), this, [this, preset](int index) {
            auto topic = index == 1 ? robot->currentData().toJsonObject()["topic"].toString() : preset->itemData(index).toString();
            if (!topic.isEmpty()) {
                for (int i = 0; i < topics->rowCount(); ++i)
                    if (cell(topics, i, 0) == topic) { preset->setCurrentIndex(0); return; }
                addRow(topics, {topic, "", "1"});
            }
            preset->setCurrentIndex(0);
        });
        row->addStretch();
        duration = new QSpinBox;
        duration->setRange(3, 60);
        duration->setValue(10);
        duration->setSuffix(" 초");
        row->addWidget(duration);
        layout->addLayout(row);
        auto *dds = new QHBoxLayout;
        ddsDomain = new QSpinBox;
        ddsDomain->setRange(-1, 232);
        ddsDomain->setSpecialValueText("환경 기본값");
        ddsDomain->setValue(-1);
        ddsInterface = new QLineEdit;
        ddsInterface->setPlaceholderText("Jetson의 ROS 통신 포트 (선택)");
        dds->addWidget(new QLabel("DDS 도메인"));
        dds->addWidget(ddsDomain);
        dds->addWidget(ddsInterface, 1);
        layout->addLayout(dds);
        auto *actions = new QHBoxLayout;
        connect(button("설치 확인", actions), &QPushButton::clicked, this, [this] { remote("설치 확인", "check"); });
        auto *verify = button("수신 검수 시작", actions);
        verify->setProperty("primary", true);
        connect(verify, &QPushButton::clicked, this, [this] {
            QJsonArray entries;
            for (int i = 0; i < topics->rowCount(); ++i) {
                auto topic = cell(topics, i, 0);
                if (topic.isEmpty()) continue;
                bool valid;
                int count = cell(topics, i, 2).toInt(&valid);
                if (!valid || count < 1) { append("토픽별 최소 수신 횟수를 확인하세요."); return; }
                entries.append(QJsonObject{{"topic", topic}, {"type", cell(topics, i, 1)}, {"min_count", count}});
            }
            if (entries.isEmpty()) { append("검수할 토픽을 추가하세요."); return; }
            QJsonObject spec{{"topics", entries}, {"seconds", duration->value()},
                             {"interface", ddsInterface->text().trimmed()}};
            if (ddsDomain->value() >= 0) spec.insert("domain", ddsDomain->value());
            auto file = jsonFile(QJsonDocument(spec));
            if (!file.isEmpty()) remote("ROS 토픽 검수", "verify", file);
        });
        actions->addStretch();
        layout->addLayout(actions);
    }

    void buildManuals()
    {
        auto *layout = page("매뉴얼");
        manuals = new QTreeWidget;
        manuals->setRootIsDecorated(false);
        manuals->setAlternatingRowColors(true);
        manuals->setHeaderLabels({"문서", "장비", "버전"});
        manuals->header()->setSectionResizeMode(0, QHeaderView::Stretch);
        layout->addWidget(manuals, 1);
        auto *row = new QHBoxLayout;
        connect(button("열기", row), &QPushButton::clicked, this, [this] { openManual(); });
        connect(manuals, &QTreeWidget::itemDoubleClicked, this, [this] { openManual(); });
        connect(button("파일 등록", row), &QPushButton::clicked, this, [this] { importManual(); });
        connect(button("등록 해제", row), &QPushButton::clicked, this, [this] {
            auto *item = manuals->currentItem();
            if (!item || item->data(0, Qt::UserRole + 1).toInt() < 0) return;
            if (QMessageBox::question(this, "등록 해제", item->text(0) + " 문서를 목록에서 뺄까요?") != QMessageBox::Yes)
                return;
            auto entries = settings.value("manuals").toList();
            entries.removeAt(item->data(0, Qt::UserRole + 1).toInt());
            settings.setValue("manuals", entries);
            settings.sync();
            reloadManuals();
        });
        row->addStretch();
        layout->addLayout(row);
        reloadManuals();
    }

    void manualRow(const QString &name, const QString &equipment, const QString &version, const QString &path, int index)
    {
        auto *item = new QTreeWidgetItem(manuals, {name, equipment, version});
        item->setData(0, Qt::UserRole, path);
        item->setData(0, Qt::UserRole + 1, index);
    }

    void reloadManuals()
    {
        manuals->clear();
        manualRow("설치 및 검수", "공통", "기본", docs + "/usage.html", -1);
        manualRow("SLAM / Navigation", "공통", "기본", docs + "/navigation.html", -1);
        int index = 0;
        for (auto entry : settings.value("manuals").toList()) {
            const auto values = entry.toMap();
            manualRow(values["name"].toString(), values["equipment"].toString(), values["version"].toString(),
                      values["path"].toString(), index++);
        }
    }

    void openManual()
    {
        auto *item = manuals->currentItem();
        if (!item) return;
        auto path = item->data(0, Qt::UserRole).toString();
        if (!QFileInfo::exists(path) || !QDesktopServices::openUrl(QUrl::fromLocalFile(path)))
            append("문서 파일을 열 수 없습니다.");
    }

    void importManual()
    {
        auto path = QFileDialog::getOpenFileName(this, "등록할 매뉴얼", {}, "문서 (*.pdf *.html *.htm *.md *.txt *.docx *.odt)");
        if (path.isEmpty()) return;
        QDialog dialog(this);
        dialog.setWindowTitle("매뉴얼 등록");
        auto *form = new QFormLayout(&dialog);
        auto *equipment = new QLineEdit(robot->currentText()), *version = new QLineEdit;
        form->addRow("장비", equipment);
        form->addRow("버전", version);
        auto *buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel);
        form->addRow(buttons);
        connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
        connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
        if (dialog.exec() != QDialog::Accepted) return;
        QFile source(path);
        const QFileInfo info(path);
        const auto folder = dataRoot + "/manuals";
        QCryptographicHash digest(QCryptographicHash::Sha256);
        if (!QDir().mkpath(folder) || !source.open(QIODevice::ReadOnly) || !digest.addData(&source)) {
            append("문서 파일을 읽을 수 없습니다."); return;
        }
        auto copy = folder + "/" + QString::fromLatin1(digest.result().toHex()) + "." + info.suffix().toLower();
        if (!QFileInfo::exists(copy) && !QFile::copy(path, copy)) {
            append("문서 사본을 만들 수 없습니다."); return;
        }
        auto entries = settings.value("manuals").toList();
        QVariantMap entry{{"name", info.fileName()}, {"equipment", equipment->text().trimmed()},
                          {"version", version->text().trimmed()}, {"path", copy}};
        if (!entries.contains(entry)) entries.append(entry);
        settings.setValue("manuals", entries);
        settings.sync();
        reloadManuals();
    }

    void start(const QString &label, const QString &program, const QStringList &arguments, bool allowStop = false)
    {
        if (busy) return;
        pending.clear();
        state->setText(label);
        tabs->setEnabled(false);
        busy = true;
        cancellable = allowStop;
        bar->setRange(0, 0);
        bar->show();
        stop->setVisible(cancellable);
        append(label);
        QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
        env.insert("PYTHONUNBUFFERED", "1");
        process.setProcessEnvironment(env);
        process.start(program, arguments);
    }

    void terminal(const QString &label, const QStringList &arguments)
    {
        auto program = QStandardPaths::findExecutable("gnome-terminal");
        if (program.isEmpty()) { append("gnome-terminal이 필요합니다."); payload.reset(); return; }
        auto folder = cache + "/logs";
        if (!QDir().mkpath(folder)) { append("로그 폴더를 만들 수 없습니다."); payload.reset(); return; }
        sessionLog = folder + "/" + QDateTime::currentDateTime().toString("yyyyMMdd-HHmmss-zzz") + "-" + arguments.value(0) + ".log";
        QFile file(sessionLog);
        if (!file.open(QIODevice::WriteOnly | QIODevice::NewOnly)) { append("작업 로그를 만들 수 없습니다."); payload.reset(); return; }
        file.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner);
        file.close();
        QStringList command{quote(backend)};
        for (const auto &arg : arguments) command.append(quote(arg));
        append("로그  " + sessionLog);
        start(label, program, {"--wait", "--", "bash", "-c",
            "script -qefc \"$1\" \"$2\"; code=$?; printf '\\n종료 코드 %s\\n' \"$code\"; read -rp 'Enter로 닫기 '; exit \"$code\"",
            "robot-studio", command.join(' '), sessionLog});
    }

    void consume(const QByteArray &bytes)
    {
        pending += bytes;
        int newline;
        while ((newline = pending.indexOf('\n')) >= 0) {
            const auto line = QString::fromUtf8(pending.left(newline)).trimmed();
            pending.remove(0, newline + 1);
            if (line.startsWith("PROGRESS ")) {
                bar->setRange(0, 100);
                bar->setValue(line.section(' ', 1, 1).toInt());
            } else if (!line.isEmpty()) {
                append(line);
            }
        }
    }

    void finish(bool ok)
    {
        if (!sessionLog.isEmpty()) {
            QFile session(sessionLog);
            if (session.open(QIODevice::ReadOnly)) {
                session.seek(qMax<qint64>(0, session.size() - 7000));
                QString output = QString::fromUtf8(session.readAll());
                output.remove(QRegularExpression("\\x1b\\[[0-9;?]*[ -/]*[@-~]"));
                output.remove('\r');
                const auto lines = output.trimmed().split('\n');
                const auto code = QRegularExpression("COMMAND_EXIT_CODE=\"(\\d+)\"").match(output);
                if (code.hasMatch()) ok = ok && code.captured(1) == "0";
                for (const auto &line : lines.mid(qMax<qsizetype>(0, lines.size() - 15)))
                    if (!line.contains("Script started") && !line.contains("Script done") && !line.contains("Enter로 닫기"))
                        append(line);
            }
        }
        sessionLog.clear();
        payload.reset();
        state->setText(ok ? "완료" : "작업 중단 또는 확인 필요");
        busy = false;
        tabs->setEnabled(true);
        stop->hide();
        bar->hide();
        scan();
    }
};

int main(int argc, char **argv)
{
    QApplication application(argc, argv);
    application.setOrganizationName("WeGo");
    application.setApplicationName("Robot Studio");
    application.setStyle("Fusion");
    QFont font = application.font();
    font.setFamily("Noto Sans CJK KR");
    font.setPointSize(10);
    application.setFont(font);
    application.setStyleSheet(R"(
        QMainWindow { background: #edf1f5; }
        QLabel#title { font-size: 24px; font-weight: 700; color: #142738; }
        QLabel[muted="true"] { color: #526276; }
        QTabWidget::pane { border: 1px solid #c8d2dd; background: #ffffff; }
        QGroupBox { font-weight: 600; border: 1px solid #c8d2dd; margin-top: 1em; padding-top: 6px; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
        QTabBar::tab { padding: 9px 19px; min-width: 65px; }
        QPushButton { min-height: 24px; padding: 3px 9px; }
        QPushButton[primary="true"] { color: white; background: #245379; border: 1px solid #245379; border-radius: 3px; }
        QPushButton[primary="true"]:disabled { background: #aab5c0; border-color: #aab5c0; }
        QComboBox, QLineEdit { min-height: 25px; }
        QPlainTextEdit { background: #142738; color: #e6edf4; border: 1px solid #142738; padding: 5px; }
    )");
    Studio studio;
    if (application.arguments().contains("--smoke-test")) {
        if (!studio.isReady()) return 2;
        studio.show();
        QTimer::singleShot(150, &application, &QApplication::quit);
        return application.exec();
    }
    studio.show();
    return application.exec();
}
