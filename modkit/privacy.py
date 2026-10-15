"""Version-pinned privacy patches for MAX 26.35.1.80662 (Windows x64).

Only apply to a verified disposable/rebuilt copy. Full SHA256 and byte guards are
mandatory. Binary changes invalidate Authenticode signatures. These patches are
statically verified; absence of traffic still requires a separate controlled test.
Keep both core analytics patches together to suppress new event creation as well
as old-queue transmission. Do not combine with blanket network/auth/call blocking.
All imported Tracer SDK entries are disabled before their prologues; merely
skipping tracer_init is insufficient because the DLL constructor creates a backend.
The call-service MyTracker initializer is a separate void entry: its borrowed
configuration arguments need no callee cleanup. Skipping it before its prologue
leaves the constructor's null StatsManager and existing null-registration paths.
This blocks the pinned initialization path if invoked; active use of that retained
SDK path and corresponding network traffic have not been demonstrated.
"""

PATCH_FILES = [{'relative_path': 'max-service/max-service.exe',
  'sha256': 'bd82b3fa0a6d24ee87bb9e0ec32ee311df7c40d8d233a71537685af00d50e0eb',
  'patches': [{'id': 'no_mytracker_stats_init',
               'offset': 0x160C9B0,
               'expected': '48895c2410',
               'replacement': 'c390909090',
               'reason': 'Return at the pinned MyTracker StatsManager void initializer RVA0x160D3B0 before prologue, argument reads or allocation. Inputs are borrowed; temporary copies and their cleanup never begin. Constructor leaves this+0x560/+0x568 null, and native registration methods retain their null-manager failure-event and vector-cleanup paths. Active use or blocked network traffic is not established.'}]},
 {'relative_path': 'config.dll',
  'sha256': '8e7d9d53f77d0f2599ed2bb5b117b3c8a4256ee361434ebd3d977b01d4685035',
  'patches': [{'id': 'mask_logs',
               'offset': 7249,
               'expected': 'e96a830200',
               'replacement': 'b001c39090',
               'reason': 'Force Config::needMaskLogs() bool true; preserve all other config initialization.'},
              {'id': 'no_error_logs_to_crash_server',
               'offset': 9334,
               'expected': 'e915810200',
               'replacement': '31c0c39090',
               'reason': 'Force Config::sendErrorLogsToCrashServer() bool false independently of config '
                         'overrides.'}]},
 {'relative_path': 'max-service/config.dll',
  'sha256': '950531233749920b60fdcb05a0465eb84f8f2acf3fbb76fc4d1279cb67f714f3',
  'patches': [{'id': 'mask_logs_service',
               'offset': 7249,
               'expected': 'e96a830200',
               'replacement': 'b001c39090',
               'reason': 'Force Config::needMaskLogs() bool true in the call service.'},
              {'id': 'no_error_logs_to_crash_server_service',
               'offset': 9334,
               'expected': 'e915810200',
               'replacement': '31c0c39090',
               'reason': 'Force Config::sendErrorLogsToCrashServer() bool false in the call service.'}]},
 {'relative_path': 'core.dll',
  'sha256': 'c59e3d753820478858a3abce85a002f63d306f4212e8d8de6a7dd42f9403945a',
  'patches': [{'id': 'no_analytics_session',
               'offset': 18227737,
               'expected': '488b9fa0010000',
               'replacement': 'e96c0000009090',
               'reason': 'updateSessionId coroutine: after completed await cleanup, skip analytics-session '
                         'construction to RVA 0x1162E8A. Constructor leaves optional has_session false; 52 '
                         'handlers already handle no-session by dropping their event. Preserve coroutine '
                         'finalization.'},
              {'id': 'no_analytics_flush',
               'offset': 17884505,
               'expected': '740c',
               'replacement': '9090',
               'reason': 'EventsSender::doFlush coroutine: choose existing already-flushing success path '
                         '(RVA 0x110F15B), set result true and continue to normal completion RVA 0x110F525. '
                         'Do not return early from coroutine. Pair with no_analytics_session to prevent new '
                         'queue growth.'}]},
 {'relative_path': 'tracernative.dll',
  'sha256': 'f61a224ff7ac5dc7647fffc533b155ccec55911f54fee8ea03a6a30e372dcbde',
  'patches': [{'id': 'no_tracer_report_error_from_here_root',
               'offset': 170656,
               'expected': '48895c2418',
               'replacement': 'c390909090',
               'reason': 'tracer_report_error_from_here: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_report_nonfatal_from_here_root',
               'offset': 171552,
               'expected': '4053',
               'replacement': 'c390',
               'reason': 'tracer_report_nonfatal_from_here: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_supports_arbitrary_dump_root',
               'offset': 173536,
               'expected': '48895c2410',
               'replacement': '31c0c39090',
               'reason': 'tracer_supports_arbitrary_dump: return false in EAX before the SDK prologue; '
                         'caller consumes bool AL.'},
              {'id': 'no_tracer_install_crash_handler_root',
               'offset': 170352,
               'expected': '4053',
               'replacement': 'c390',
               'reason': 'tracer_install_crash_handler: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_upload_async_root',
               'offset': 173680,
               'expected': '4053',
               'replacement': 'c390',
               'reason': 'tracer_upload_async: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_log_root',
               'offset': 170416,
               'expected': '48895c2410',
               'replacement': 'c390909090',
               'reason': 'tracer_log: no-op at public SDK entry before original prologue/locks/argument '
                         'reads; all statically imported MAX call sites ignore its return value. Preserve '
                         'caller cleanup.'},
              {'id': 'no_tracer_set_userid_root',
               'offset': 173296,
               'expected': '48895c2410',
               'replacement': 'c390909090',
               'reason': 'tracer_set_userid: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_set_platform_info_root',
               'offset': 172448,
               'expected': '4c8bdc',
               'replacement': 'c39090',
               'reason': 'tracer_set_platform_info: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_init_root',
               'offset': 170272,
               'expected': '4889542410',
               'replacement': 'c390909090',
               'reason': 'tracer_init: no-op at public SDK entry before original prologue/locks/argument '
                         'reads; all statically imported MAX call sites ignore its return value. Preserve '
                         'caller cleanup.'},
              {'id': 'no_tracer_enable_logging_to_console_root',
               'offset': 169632,
               'expected': '488d0d09ee1c00',
               'replacement': 'c3909090909090',
               'reason': 'tracer_enable_logging_to_console: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_hardfix_crashclient_root',
               'offset': 170208,
               'expected': '4053',
               'replacement': 'c390',
               'reason': 'tracer_hardfix_crashclient: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'}]},
 {'relative_path': 'max-service/tracernative.dll',
  'sha256': '90da308d64ac461490cfe7355cd16105492199b1b74123c29ee285b36cf18341',
  'patches': [{'id': 'no_tracer_collect_user_sample_service',
               'offset': 169120,
               'expected': '48895c2410',
               'replacement': 'c390909090',
               'reason': 'tracer_collect_user_sample: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_set_proxy_info_service',
               'offset': 172704,
               'expected': '48895c2418',
               'replacement': 'c390909090',
               'reason': 'tracer_set_proxy_info: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_install_crash_handler_service',
               'offset': 170352,
               'expected': '4053',
               'replacement': 'c390',
               'reason': 'tracer_install_crash_handler: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_upload_async_service',
               'offset': 173680,
               'expected': '4053',
               'replacement': 'c390',
               'reason': 'tracer_upload_async: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_set_environment_service',
               'offset': 171936,
               'expected': '48895c2410',
               'replacement': 'c390909090',
               'reason': 'tracer_set_environment: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_set_user_sample_custom_property_service',
               'offset': 173104,
               'expected': '48895c2408',
               'replacement': 'c390909090',
               'reason': 'tracer_set_user_sample_custom_property: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_set_key_service',
               'offset': 172368,
               'expected': '4889542410',
               'replacement': 'c390909090',
               'reason': 'tracer_set_key: no-op at public SDK entry before original prologue/locks/argument '
                         'reads; all statically imported MAX call sites ignore its return value. Preserve '
                         'caller cleanup.'},
              {'id': 'no_tracer_set_platform_info_service',
               'offset': 172448,
               'expected': '4c8bdc',
               'replacement': 'c39090',
               'reason': 'tracer_set_platform_info: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_init_service',
               'offset': 170272,
               'expected': '4889542410',
               'replacement': 'c390909090',
               'reason': 'tracer_init: no-op at public SDK entry before original prologue/locks/argument '
                         'reads; all statically imported MAX call sites ignore its return value. Preserve '
                         'caller cleanup.'},
              {'id': 'no_tracer_enable_logging_to_callable_service',
               'offset': 169600,
               'expected': '488bd1',
               'replacement': 'c39090',
               'reason': 'tracer_enable_logging_to_callable: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_log_service',
               'offset': 170416,
               'expected': '48895c2410',
               'replacement': 'c390909090',
               'reason': 'tracer_log: no-op at public SDK entry before original prologue/locks/argument '
                         'reads; all statically imported MAX call sites ignore its return value. Preserve '
                         'caller cleanup.'},
              {'id': 'no_tracer_hardfix_crashclient_service',
               'offset': 170208,
               'expected': '4053',
               'replacement': 'c390',
               'reason': 'tracer_hardfix_crashclient: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'},
              {'id': 'no_tracer_set_userid_service',
               'offset': 173296,
               'expected': '48895c2410',
               'replacement': 'c390909090',
               'reason': 'tracer_set_userid: no-op at public SDK entry before original '
                         'prologue/locks/argument reads; all statically imported MAX call sites ignore its '
                         'return value. Preserve caller cleanup.'}]}]
