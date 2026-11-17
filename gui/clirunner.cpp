#include "clirunner.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcessEnvironment>
#include <QStandardPaths>

Cli::Cli(QObject *parent) : QObject(parent)
{
    m_python = qEnvironmentVariable("DC1_PYTHON");
    if (m_python.isEmpty()) {
#ifdef Q_OS_WIN
        m_python = QStringLiteral("python");
#else
        m_python = QStringLiteral("python3");
#endif
    }
}

void Cli::setPythonPath(const QString &p)
{
    if (p == m_python)
        return;
    m_python = p;
    emit pythonPathChanged();
}

QString Cli::resolveCli()
{
    // 1) 环境变量  2) 可执行文件旁边  3) 源码树
    const QString env = qEnvironmentVariable("DC1_CLI");
    if (!env.isEmpty() && QFileInfo::exists(env))
        return QDir::cleanPath(env);

    const QString appDir = QCoreApplication::applicationDirPath();
    const QStringList candidates = {
        appDir + "/cli/dc1ctl.py",
        appDir + "/../cli/dc1ctl.py",
        appDir + "/../../cli/dc1ctl.py",
        QStringLiteral(DC1_SOURCE_DIR) + "/cli/dc1ctl.py",
    };
    for (const auto &c : candidates) {
        if (QFileInfo::exists(c))
            return QDir::cleanPath(c);
    }
    return QStringLiteral(DC1_SOURCE_DIR) + "/cli/dc1ctl.py";
}

QString Cli::cliPath() const { return resolveCli(); }

void Cli::run(const QStringList &args)
{
    if (m_proc) {                 // 单通道：上一单没完就拒绝新单
        emit output(QStringLiteral("[busy] 上一条命令还没结束"));
        emit finished(false, QStringLiteral("{\"err\":\"busy\"}"));
        return;
    }

    m_out.clear();
    m_proc = new QProcess(this);
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    env.insert("PYTHONIOENCODING", "utf-8");
    env.insert("PYTHONUNBUFFERED", "1");
    m_proc->setProcessEnvironment(env);
    m_proc->setProcessChannelMode(QProcess::MergedChannels);

    connect(m_proc, &QProcess::readyReadStandardOutput, this, [this]() {
        const QByteArray chunk = m_proc->readAllStandardOutput();
        m_out += chunk;
        const QList<QByteArray> lines = chunk.split('\n');
        for (const QByteArray &l : lines) {
            const QString s = QString::fromUtf8(l).trimmed();
            if (!s.isEmpty())
                emit output(s);
        }
    });

    connect(m_proc, &QProcess::errorOccurred, this, [this](QProcess::ProcessError e) {
        if (e == QProcess::FailedToStart) {
            emit output(QStringLiteral("[error] 启动 Python 失败：") + m_python);
            const bool wasBusy = true;
            Q_UNUSED(wasBusy);
            m_proc->deleteLater();
            m_proc = nullptr;
            emit busyChanged();
            emit finished(false, QStringLiteral("{\"err\":\"python_not_found\"}"));
        }
    });

    connect(m_proc, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished), this,
            [this](int code, QProcess::ExitStatus status) {
                const QByteArray out = m_out.trimmed();
                const bool ok = (status == QProcess::NormalExit && code == 0);
                QString json = QString::fromUtf8(out);

                // 只把最后一段 JSON 交给 QML（CLI 可能先打日志）
                const int obj = json.indexOf('{');
                const int arr = json.indexOf('[');
                int start = -1;
                if (obj >= 0 && arr >= 0)
                    start = qMin(obj, arr);
                else
                    start = qMax(obj, arr);
                if (start > 0)
                    json = json.mid(start);

                m_proc->deleteLater();
                m_proc = nullptr;
                emit busyChanged();
                emit finished(ok, json);
            });

    emit busyChanged();
    m_proc->start(m_python, QStringList{QStringLiteral("-u"), cliPath()} + args);
}
