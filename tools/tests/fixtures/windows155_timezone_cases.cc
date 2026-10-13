
namespace {
using blink::TimeZoneController;
constexpr const char* kLaunch = "America/New_York";
constexpr const char* kOverride = "Asia/Tokyo";
constexpr const char* kInvalid = "Not/A_Time_Zone";

std::string Id(const icu::TimeZone& timezone) {
  icu::UnicodeString id;
  timezone.getID(id);
  std::string result;
  id.toUTF8String(result);
  return result;
}

void ExpectDefault(const char* id, const blink::IsolateCache& first,
                   const blink::IsolateCache& second) {
  assert(blink::GetCurrentTimezoneId() == blink::String(id));
  assert(Id(*first.timezone) == id);
  assert(Id(*second.timezone) == id);
}

void ExpectDst(const icu::TimeZone& timezone) {
  constexpr UDate dates[] = {1772953140000., 1772953200000.,
                            1793512740000., 1793512800000.};
  constexpr int offsets[] = {300, 240, 240, 300};
  const char* clocks[] = {"01:59", "03:00", "01:59", "01:00"};
  UErrorCode status = U_ZERO_ERROR;
  icu::SimpleDateFormat formatter(icu::UnicodeString("HH:mm"),
                                icu::Locale::getUS(), status);
  assert(U_SUCCESS(status));
  formatter.setTimeZone(timezone);
  for (size_t i = 0; i != 4; ++i) {
    int32_t raw = 0, dst = 0;
    timezone.getOffset(dates[i], false, raw, dst, status);
    assert(U_SUCCESS(status));
    assert(-(raw + dst) / 60000 == offsets[i]);
    icu::UnicodeString formatted;
    formatter.format(dates[i], formatted);
    std::string text;
    formatted.toUTF8String(text);
    assert(text == clocks[i]);
  }
}

void Install(const char* timezone) {
  base::flat_map<std::string, std::string> config;
  if (timezone) config["uxr-timezone"] = timezone;
  RenderThreadImpl().SetUxrConfig(config, 1);
}

void Monitor(const char* timezone) {
  TimeZoneController::ChangeTimeZoneForTesting(blink::String(timezone));
}

void ExpectLaunch(blink::IsolateCache& first, blink::IsolateCache& second,
                  blink::IsolateCache& worker) {
  ExpectDefault(kLaunch, first, second);
  ExpectDst(*first.timezone);
  ExpectDst(*second.timezone);
  blink::DrainWorkers();
  assert(Id(*worker.timezone) == kLaunch);
  ExpectDst(*worker.timezone);
  blink::IsolateCache new_worker;
  assert(Id(*new_worker.timezone) == kLaunch);
  ExpectDst(*new_worker.timezone);
}
}

int main(int argc, char** argv) {
  assert(argc == 3);
  const std::string test = argv[1];
  base::FeatureList::lazy = std::string(argv[2]) == "1";
  icu::TimeZone::adoptDefault(icu::TimeZone::createTimeZone("UTC"));
  blink::IsolateCache first, second, worker;
  blink::main_isolates = {&first, &second};
  blink::WorkerThread::workers = {&worker};

  if (test == "monitor-early" || test == "monitor-late" || test == "replay") {
    if (test == "monitor-early") {
      Monitor("UTC");
      ExpectDefault("UTC", first, second);
      blink::DrainWorkers();
    }
    Install(kLaunch);
    assert(first.notifications > 0 && second.notifications > 0);
    ExpectLaunch(first, second, worker);
    if (test == "replay") Install(kLaunch);
    Monitor("Europe/London");
    ExpectLaunch(first, second, worker);
  } else if (test == "no-timezone" || test == "empty-timezone") {
    Install(test == "no-timezone" ? nullptr : "");
    ExpectDefault("UTC", first, second);
    assert(first.notifications == 0 && second.notifications == 0);
    assert(blink::pending.empty());
    Monitor("Europe/London");
    ExpectDefault("Europe/London", first, second);
    blink::DrainWorkers();
    assert(Id(*worker.timezone) == "Europe/London");
    auto result = TimeZoneController::SetTimeZoneOverride(kOverride);
    assert(result.handle);
    Monitor("America/Los_Angeles");
    ExpectDefault(kOverride, first, second);
    result.handle.reset();
    ExpectDefault("America/Los_Angeles", first, second);
  } else if (test == "override-before" || test == "override-after" ||
             test == "override-same") {
    if (test != "override-before") Install(kLaunch);
    const char* override_id = test == "override-same" ? kLaunch : kOverride;
    auto result = TimeZoneController::SetTimeZoneOverride(override_id);
    assert(result.status == TimeZoneController::TimeZoneOverrideStatus::kSuccess);
    assert(result.handle);
    const int notifications = first.notifications;
    if (test == "override-before") Install(kLaunch);
    Monitor("Europe/London");
    assert(first.notifications == notifications);
    ExpectDefault(override_id, first, second);
    blink::DrainWorkers();
    assert(Id(*worker.timezone) == override_id);
    auto competing = TimeZoneController::SetTimeZoneOverride("Europe/Paris");
    assert(competing.status == TimeZoneController::TimeZoneOverrideStatus::kAlreadyInEffect);
    assert(!competing.handle);
    auto same = TimeZoneController::SetTimeZoneOverride(override_id);
    assert(same.status == TimeZoneController::TimeZoneOverrideStatus::kSuccess);
    assert(!same.handle);
    result.handle->change("Europe/Paris");
    ExpectDefault("Europe/Paris", first, second);
    result.handle->change(kInvalid);
    ExpectDefault("Europe/Paris", first, second);
    result.handle.reset();
    ExpectLaunch(first, second, worker);
  } else if (test == "invalid-default" || test == "invalid-under-override") {
    Install(kLaunch);
    ExpectLaunch(first, second, worker);
    std::unique_ptr<TimeZoneController::TimeZoneOverride> handle;
    if (test == "invalid-under-override")
      handle = TimeZoneController::SetTimeZoneOverride(kOverride).handle;
    const int notifications = first.notifications;
    assert(!TimeZoneController::SetDefaultTimeZone(kInvalid));
    assert(!TimeZoneController::SetDefaultTimeZone(""));
    assert(first.notifications == notifications);
    ExpectDefault(handle ? kOverride : kLaunch, first, second);
    handle.reset();
    ExpectLaunch(first, second, worker);
  } else if (test == "invalid-launch") {
    bool rejected = false;
    try { Install(kInvalid); } catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
    ExpectDefault("UTC", first, second);
    assert(first.notifications == 0 && blink::pending.empty());
  } else if (test == "immutable-snapshot") {
    Install(kLaunch);
    bool rejected = false;
    try { Install(kOverride); } catch (const std::runtime_error&) { rejected = true; }
    assert(rejected);
    ExpectLaunch(first, second, worker);
  } else {
    assert(false);
  }
  blink::DrainWorkers();
  std::cout << test << " passed\n";
}
