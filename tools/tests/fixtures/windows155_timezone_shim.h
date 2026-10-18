// Source-derived controller tests use real ICU and explicit scheduler/V8 shims.
#include <cassert>
#include <cstdint>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
#include <unicode/timezone.h>
#include <unicode/smpdtfmt.h>
#include <unicode/locid.h>

class CheckResult {
 public:
  explicit CheckResult(bool ok) : ok_(ok) {}
  template <class T> CheckResult& operator<<(const T&) { return *this; }
  ~CheckResult() noexcept(false) { if (!ok_) throw std::runtime_error("CHECK"); }
 private:
  bool ok_;
};
#define CHECK(condition) CheckResult(static_cast<bool>(condition))
#define DCHECK(condition) assert(condition)
#define VLOG(level) CheckResult(true)
#define CORE_EXPORT
#define GUARDED_BY(lock)
#define DEFINE_THREAD_SAFE_STATIC_LOCAL(type, name, args) static type name
#define FROM_HERE 0

namespace base {
class Lock {
 public:
  void Acquire() { assert(!held_); held_ = true; }
  void Release() { assert(held_); held_ = false; }
  void AssertAcquired() const { assert(held_); }
 private:
  bool held_ = false;
};
class AutoLock {
 public:
  explicit AutoLock(Lock& lock) : lock_(lock) { lock_.Acquire(); }
  ~AutoLock() { lock_.Release(); }
 private:
  Lock& lock_;
};
struct FeatureList {
  static inline bool lazy = false;
  static bool IsEnabled(int) { return lazy; }
};
template <class K, class V> using flat_map = std::map<K, V>;
class UxrConfig {
 public:
  static UxrConfig& GetInstance() { static UxrConfig config; return config; }
  bool SetAll(const flat_map<std::string, std::string>& config, uint32_t version) {
    if (version != 1 || (initialized_ && values_ != config)) return false;
    values_ = config;
    initialized_ = true;
    return true;
  }
  bool Has(const std::string& key) const { return values_.contains(key); }
  std::string Get(const std::string& key) const {
    const auto found = values_.find(key);
    return found == values_.end() ? "" : found->second;
  }
  std::string ValidationError() const { return "invalid snapshot"; }
 private:
  bool initialized_ = false;
  flat_map<std::string, std::string> values_;
};
}
namespace blink {
class String {
 public:
  String() = default;
  String(const char* value) : value_(value), null_(false) {}
  bool empty() const { return value_.empty(); }
  bool IsNull() const { return null_; }
  std::string Ascii() const { return value_; }
  friend bool operator==(const String& a, const String& b) { return a.value_ == b.value_; }
 private:
  std::string value_;
  bool null_ = true;
};
}  // namespace blink

#include "third_party/blink/public/platform/web_string.h"

namespace blink {
class StringImpl {
 public:
  explicit StringImpl(std::string_view value) : value(std::string(value).c_str()) {}
  String value;
};
WebString::WebString() = default;
WebString::~WebString() = default;
WebString::WebString(const WebString&) = default;
WebString::WebString(WebString&&) = default;
WebString& WebString::operator=(const WebString&) = default;
WebString& WebString::operator=(WebString&&) = default;
WebString WebString::FromUtf8(std::string_view value) {
  WebString result;
  result.impl_ = std::make_shared<StringImpl>(value);
  return result;
}
WebString::operator String() const { return impl_ ? impl_->value : String(); }
inline bool IsMainThread() { return true; }
inline constexpr int kLazyBlinkTimezoneInit = 0;
inline String GetCurrentTimezoneId() {
  std::unique_ptr<icu::TimeZone> timezone(icu::TimeZone::createDefault());
  icu::UnicodeString id;
  timezone->getID(id);
  std::string utf8;
  id.toUTF8String(utf8);
  return String(utf8.c_str());
}
inline std::vector<std::function<void()>> pending;
struct IsolateCache {
  std::unique_ptr<icu::TimeZone> timezone{icu::TimeZone::createDefault()};
  int notifications = 0;
  void Notify() { timezone.reset(icu::TimeZone::createDefault()); ++notifications; }
};
inline std::vector<IsolateCache*> main_isolates;
inline void NotifyTimezoneChangeOnMainThread() {
  for (auto* isolate : main_isolates) isolate->Notify();
}
enum class TaskType { kInternalDefault };
struct WorkerThread {
  static inline std::vector<IsolateCache*> workers;
  static void CallOnAllWorkerThreads(void (*)(WorkerThread*), TaskType) {
    for (auto* worker : workers) pending.push_back([worker] { worker->Notify(); });
  }
};
inline void NotifyTimezoneChangeOnWorkerThread(WorkerThread*) {}
inline void DrainWorkers() {
  auto tasks = std::move(pending);
  pending.clear();
  for (auto& task : tasks) task();
}
inline int MainThreadTaskRunnerRestricted() { return 0; }
struct Thread {
  static Thread* MainThread() { static Thread thread; return &thread; }
  Thread* GetTaskRunner(int) { return this; }
  void PostTask(int, std::function<void()> callback) { pending.push_back(std::move(callback)); }
};
template <class T> auto CrossThreadBindOnce(T callback) { return callback; }
template <class T> auto ConvertToBaseOnceCallback(T callback) { return callback; }
}
namespace device::mojom::blink {
class TimeZoneMonitorClient {
 public:
  virtual ~TimeZoneMonitorClient() = default;
  virtual void OnTimeZoneChange(const ::blink::String&) = 0;
};
}
namespace mojo {
template <class T> struct Receiver { explicit Receiver(T*) {} };
}
class RenderThreadImpl {
 public:
  void SetUxrConfig(const base::flat_map<std::string, std::string>&, uint32_t);
};
