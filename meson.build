project('zimaos-packages', version: '0.1.0', meson_version: '>=1.3.0', license: 'MIT')

python = import('python').find_installation('python3')
if not python.language_version().version_compare('>=3.12')
  error('Python 3.12 or newer is required')
endif
fs = import('fs')
configure_tool = files('tools/configure.py')
worker = files('tools/pipeline.py')
worker_inputs = files('tools/pipeline.py', 'tools/common.py', 'tools/manifest.py')
configure_inputs = files('tools/configure.py', 'tools/manifest.py')
foreach path : configure_inputs
  fs.read(path)
endforeach
clean_env = environment()
clean_env.set('PYTHONDONTWRITEBYTECODE', '1')

run_target('list', command: [python, configure_tool, 'list'], env: clean_env)
test('tooling', python,
  args: ['-m', 'unittest', 'discover', '-s', meson.project_source_root() / 'tests', '-v'],
  suite: 'unit', env: clean_env, timeout: 240)

selected = get_option('packages')
if 'all' in selected
  if selected.length() != 1
    error('The all selector cannot be combined with package names')
  endif
  selected = run_command(python, configure_tool, 'list', check: true, env: clean_env).stdout().strip().split('\n')
endif
if selected.length() > 0 and (get_option('target') == '' or get_option('os_version') == '')
  error('Selected packages require -Dtarget and -Dos_version')
endif
if get_option('source_cache') != '' and not fs.is_absolute(get_option('source_cache'))
  error('source_cache must be an absolute directory')
endif
if selected.length() > 0
  squashfs = find_program('mksquashfs')
endif
source_targets = []
image_targets = []
bundle_targets = []
foreach name : selected
  arguments = ['--package', name, '--target', get_option('target'), '--os-version', get_option('os_version')]
  records = run_command(python, configure_tool, 'inspect', arguments, check: true, env: clean_env).stdout().strip().split('\n')
  manifest = files('packages' / name / 'package.json')
  target = files('targets' / (get_option('target') + '.json'))
  recipe_inputs = []
  payload_inputs = []
  artifact_names = []
  test_suites = []
  notice_inputs = []
  notice_outputs = []
  source_name = ''
  foreach record : records
    entry = record.split('\t')
    if entry[0] == 'source'
      source_name = name + '.source-' + entry[1]
    elif entry[0] == 'recipe'
      recipe_inputs += files(entry[1])
    elif entry[0] == 'payload'
      payload_inputs += files(entry[1])
    elif entry[0] == 'artifact'
      artifact_names += name + '.' + entry[1]
    elif entry[0] == 'test'
      test_suites += entry[1]
    elif entry[0] == 'notice'
      notice_inputs += files(entry[1])
      notice_outputs += name + '.NOTICE'
    endif
  endforeach
  specs = {}
  foreach phase : ['fetch', 'stage', 'assemble', 'image', 'test']
    specs += {phase: configure_file(
      input: [manifest, target], output: name + '.' + phase + '.json',
      command: [python, configure_tool, 'spec', arguments, '--phase', phase,
                '--threads', get_option('image_threads').to_string()],
      capture: true,
    )}
  endforeach
  source = custom_target(name + '-download', input: specs['fetch'], output: source_name,
    command: [python, worker, 'fetch', '--spec', '@INPUT@', '--output', '@OUTPUT@', '--cache', get_option('source_cache')],
    depend_files: worker_inputs, env: clean_env)
  staged = custom_target(name + '-stage', input: [source, specs['stage']],
    output: [name + '.stage.tar', name + '.details.json'] + artifact_names,
    command: [python, worker, 'stage', '--spec', specs['stage'], '--source', source,
              '--archive', '@OUTPUT0@', '--details', '@OUTPUT1@',
              '--output-dir', meson.project_build_root(), '--work-dir', meson.project_build_root() / 'work' / name],
    depend_files: worker_inputs + recipe_inputs, env: clean_env)
  assembled = custom_target(name + '-assemble', input: [staged[0], staged[1], specs['assemble'], source],
    output: [name + '.image.tar', name + '.module.json', name + '.build-info.json'] + notice_outputs,
    command: [python, worker, 'assemble', '--spec', specs['assemble'], '--archive', staged[0],
              '--details', staged[1], '--output-dir', meson.project_build_root()],
    depend_files: worker_inputs + payload_inputs + notice_inputs + files('LICENSE', 'meson.build', 'meson.options') + configure_inputs,
    env: clean_env)
  raw = custom_target(name + '-image', input: [assembled[0], specs['image']], output: name + '.raw',
    command: [python, worker, 'image', '--spec', specs['image'], '--archive', assembled[0],
              '--output', '@OUTPUT@', '--mksquashfs', squashfs],
    depend_files: worker_inputs, env: clean_env)
  release_files = [raw, source, assembled[1], assembled[2]]
  if notice_outputs.length() > 0
    release_files += assembled[3]
  endif
  foreach index : range(artifact_names.length())
    release_files += staged[index + 2]
  endforeach
  checksums = custom_target(name + '-checksums', input: release_files, output: name + '.SHA256SUMS',
    command: [python, worker, 'checksums', '--output', '@OUTPUT@', '@INPUT@'],
    depend_files: worker_inputs, build_by_default: true, env: clean_env)
  alias_target(name, checksums)
  bundle = custom_target(name + '-bundle', input: release_files + [checksums, specs['image']],
    output: name + '.bundle.tar.gz',
    command: [python, worker, 'bundle', '--spec', specs['image'], '--output', '@OUTPUT@', release_files, checksums],
    depend_files: worker_inputs, env: clean_env)
  bundle_targets += bundle
  source_targets += source
  image_targets += checksums
  foreach suite : test_suites
    if suite == 'smoke' or (suite == 'integration' and get_option('integration_tests'))
      test(name + '-' + suite, python,
        args: [worker, 'test', '--spec', specs['test'], '--suite', suite, '--image', raw,
               '--work-dir', meson.project_build_root() / 'test-work'],
        depends: checksums, suite: suite, env: clean_env, timeout: 240,
        is_parallel: suite != 'integration')
    endif
  endforeach
endforeach
if selected.length() > 0
  alias_target('sources', source_targets)
  alias_target('extensions', image_targets)
  alias_target('bundles', bundle_targets)
endif
summary({'Packages': selected, 'Target': get_option('target'), 'OS version': get_option('os_version'),
         'Integration tests': get_option('integration_tests')})
