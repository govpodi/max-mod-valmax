#include "decode_queue.h"
#include <QBuffer>
#include <QColorSpace>
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QEvent>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPointer>
#include <QSemaphore>
#include <QThread>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <future>
#include <stdexcept>
#include <vector>

using namespace valmax::media;
namespace {
int checks=0;
void require(bool value,const char* label){++checks;if(!value){std::fprintf(stderr,"FAILED: %s\n",label);std::exit(1);}}
template<class F>void until(F done){QElapsedTimer timer;timer.start();while(!done()&&timer.elapsed()<3000){QCoreApplication::processEvents();QThread::msleep(1);}require(done(),"asynchronous operation completed");}
void flush(){for(int n=0;n<8;++n)QCoreApplication::processEvents();}
QByteArray png(){QImage image(50,30,QImage::Format_ARGB32);image.fill(Qt::blue);QByteArray data;QBuffer buffer(&data);require(buffer.open(QIODevice::WriteOnly)&&image.save(&buffer,"PNG"),"synthetic PNG");return data;}
Result<Thumbnail> tiny(){QImage image(2,2,QImage::Format_ARGB32_Premultiplied);image.setColorSpace(QColorSpace::SRgb);image.fill(Qt::red);return {Thumbnail{image,QSize(2,2)},{}};}
const QString Mime=QStringLiteral("image/png");
}
int main(int argc,char** argv){
    QCoreApplication app(argc,argv);
    const auto bytes=png();const auto box=QSize(240,180);
    {
        DecodeQueue queue;int completed=0;
        for(int n=0;n<4;++n){
            auto ticket=queue.submit(quint64(n+1),bytes,Mime,box,[&](quint64,DecodeResult result){
                require(QThread::currentThread()==app.thread(),"delivery on owner thread");
                require(result.state==DecodeState::Completed&&result.thumbnail.has_value(),"actual native decoder result");
                require(result.thumbnail->image.pixelColor(0,0)==QColor(Qt::blue),"actual decoded pixel");++completed;});
            require(bool(ticket),"actual decoder admitted");
        }
        until([&]{return completed==4;});require(queue.snapshot().value->pending==0,"actual queue drained");
        require(!queue.submit(0,bytes,Mime,box,[](auto,auto){}),"zero scope rejected");
        require(!queue.submit(1,bytes,Mime,QSize(513,1),[](auto,auto){}),"oversized decode box rejected");
        require(!queue.submit(1,{},Mime,box,[](auto,auto){}),"bad encoded input rejected before worker");
        auto wrong=std::async(std::launch::async,[&]{return !queue.submit(1,bytes,Mime,box,[](auto,auto){})&&!queue.snapshot()&&!queue.cancel(1)&&!queue.cancel_scope(1)&&!queue.shutdown();});
        require(wrong.get(),"wrong-thread operations reject");
    }
    {
        QSemaphore gate;std::atomic<int> started{0},running{0},maximum{0};int callbacks=0,drained=0;
        DecodeQueue queue(nullptr,[&](const auto&,const auto&,QSize){
            ++started;const int active=++running;int prior=maximum.load();while(prior<active&&!maximum.compare_exchange_weak(prior,active)){}
            gate.acquire();--running;return tiny();},[&]{++drained;});
        const auto callback=[&](quint64,DecodeResult result){require(result.state==DecodeState::Cancelled&&!result.thumbnail,"cancel has no image");++callbacks;};
        const auto first=queue.submit(7,bytes,Mime,box,callback);const auto second=queue.submit(7,bytes,Mime,box,callback);
        require(first&&second,"two initial workers admitted");until([&]{return started.load()==2;});
        for(int n=0;n<8;++n)require(bool(queue.submit(7,bytes,Mime,box,callback)),"eight waiting jobs admitted");
        require(!queue.submit(7,bytes,Mime,box,callback),"pending capacity rejected");
        require(queue.snapshot().value->active==2&&queue.snapshot().value->waiting==8,"two active eight waiting");
        require(queue.cancel_scope(7),"cancel complete scope");until([&]{return callbacks==8;});
        require(queue.snapshot().value->active==2&&started==2,"cancelled running codecs retain slots");
        queue.shutdown();flush();require(drained==0,"drain waits for actual workers");
        require(!queue.submit(8,bytes,Mime,box,callback),"shutdown rejects new work");
        gate.release(2);until([&]{return callbacks==10&&drained==1;});
        require(maximum==2&&started==2&&running==0,"concurrency bound and waiting cancellation");
        queue.shutdown();flush();require(drained==1,"drained notification once");
    }
    {
        int callbacks=0;DecodeQueue queue(nullptr,[](const auto&,const auto&,QSize)->Result<Thumbnail>{throw std::runtime_error("synthetic");});
        queue.submit(1,bytes,Mime,box,[&](quint64,DecodeResult result){require(result.state==DecodeState::Failed&&!result.thumbnail&&result.error.code==Code::ProviderUnavailable,"decoder exception finite failure");++callbacks;});
        until([&]{return callbacks==1;});
    }
    {
        int callbacks=0;QPointer<DecodeQueue> queue=new DecodeQueue(nullptr,[](const auto&,const auto&,QSize){return tiny();});
        queue->submit(1,bytes,Mime,box,[&](quint64,DecodeResult){++callbacks;auto accepted=queue->submit(2,bytes,Mime,box,[&](quint64,DecodeResult){++callbacks;delete queue.data();});require(bool(accepted),"callback reentrant submit");});
        until([&]{return callbacks==2&&queue.isNull();});flush();
    }
    {
        QSemaphore gate;std::atomic<int> started{0};int callbacks=0;
        auto* queue=new DecodeQueue(nullptr,[&](const auto&,const auto&,QSize){++started;gate.acquire();return tiny();});
        queue->submit(1,bytes,Mime,box,[&](auto,auto){++callbacks;});until([&]{return started==1;});
        queue->submit(1,bytes,Mime,box,[&](auto,auto){++callbacks;}); // Queued, no event loop pass.
        auto release=std::async(std::launch::async,[&]{QThread::msleep(20);gate.release();});
        delete queue;release.get();flush();
        require(started==1&&callbacks==0,"destructor joins active worker without GUI delivery; skips queued work");
    }
    {
        int completed=0,cancelled=0;DecodeQueue queue(nullptr,[](const auto&,const auto&,QSize){return tiny();});
        auto cb=[&](quint64,DecodeResult result){if(result.state==DecodeState::Completed)++completed;else if(result.state==DecodeState::Cancelled)++cancelled;};
        queue.submit(1,bytes,Mime,box,cb);queue.submit(2,bytes,Mime,box,cb);
        require(queue.cancel_scope(1),"scope cancellation requested");until([&]{return completed==1&&cancelled==1;});
        require(!queue.cancel_scope(999),"unknown scope no effect");
    }
    {
        std::vector<char> backing(bytes.begin(),bytes.end());
        std::vector<QChar> mimeBacking(Mime.begin(),Mime.end());
        int callbacks=0;
        DecodeQueue queue(nullptr,[&](const QByteArray& input,const QString& mime,QSize){
            if(input!=bytes||mime!=Mime||input.capacity()>input.size()+64)return Result<Thumbnail>{{},{Code::InvalidInput,400,{}}};
            return tiny();});
        auto callback=[&](quint64,DecodeResult result){require(result.state==DecodeState::Completed,"async inputs owned and compact");++callbacks;};
        require(bool(queue.submit(1,QByteArray::fromRawData(backing.data(),qsizetype(backing.size())),
            QString::fromRawData(mimeBacking.data(),qsizetype(mimeBacking.size())),box,callback)),"borrowed input admitted");
        std::fill(backing.begin(),backing.end(),'x');std::fill(mimeBacking.begin(),mimeBacking.end(),QChar(u'x'));
        QByteArray reserved=bytes;reserved.reserve(10*1024*1024);
        require(bool(queue.submit(1,reserved,Mime,box,callback)),"large reserve bounded payload admitted");
        until([&]{return callbacks==2;});
    }
    {
        QSemaphore gate;std::atomic<int> started{0};int callbacks=0,destroyed=0;
        auto* queue=new DecodeQueue(nullptr,[&](const auto&,const auto&,QSize){++started;gate.acquire();return tiny();});
        QObject::connect(queue,&QObject::destroyed,[&](QObject* object){
            ++destroyed;QCoreApplication::sendPostedEvents(object,QEvent::MetaCall);QCoreApplication::processEvents();});
        queue->submit(1,bytes,Mime,box,[&](auto,auto){++callbacks;});until([&]{return started==1;});
        auto release=std::async(std::launch::async,[&]{QThread::msleep(20);gate.release();});
        delete queue;release.get();flush();
        require(destroyed==1&&callbacks==0,"nested destroyed observer cannot deliver queued worker completion");
    }
    const QJsonObject proof{{QStringLiteral("passed"),true},{QStringLiteral("checks"),checks},
        {QStringLiteral("qt"),QString::fromLatin1(qVersion())},{QStringLiteral("networkUsed"),false}};
    std::puts(QJsonDocument(proof).toJson(QJsonDocument::Compact).constData());return 0;
}
