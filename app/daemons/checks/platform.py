# internal imports
from scdata._config import config
from scdata import Device
from scdata.utils import std_out
import sys

# Config settings
config._out_level = 'DEBUG'
config._timestamp = True

def platform_check(device, dryrun = False):
    '''
        This function processes a device from SC API assuming there
        is postprocessing information in it and that it's valid for doing
        so
    '''
    std_out(f'[SCFLOW] Processing instance for device {device}')
    # Create device from SC API
    d = Device(descriptor = {'source': 'api', 'id': f'{device}'})
    results = {}
    if d.validate():
        # Load
        results['basic'] = d.checks(level=1)
        if results['basic']['status'] == 200:
            if d.load(only_unprocessed=False, options = {'resample': False}):
                # Process it
                results['advanced']=d.checks(level=2)
    else:
        std_out(f'[SCFLOW] Device {device} not valid', 'ERROR')
    std_out(f'[SCFLOW] Concluded job for {device}')
    return results

if __name__ == '__main__':

    if '-h' in sys.argv or '--help' in sys.argv or '-help' in sys.argv:
        print('platform_check: Check health of a device of SC API')
        print('USAGE:\n\platform_check.py --device <device-number> [options]')
        print('options:')
        print('--dry-run: dry run')
        sys.exit()

    if '--dry-run' in sys.argv: dry_run = True
    else: dry_run = False

    if '--device' in sys.argv:
        device = int(sys.argv[sys.argv.index('--device')+1])
        results = platform_check(device, dry_run)

        print (results)
