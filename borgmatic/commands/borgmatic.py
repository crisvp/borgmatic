
from argparse import ArgumentParser
import logging
import os
from subprocess import CalledProcessError
import sys

from borgmatic.borg import check as borg_check, create as borg_create, prune as borg_prune, \
     list as borg_list, info as borg_info
from borgmatic.commands import hook
from borgmatic.config import collect, convert, validate
from borgmatic.signals import configure_signals
from borgmatic.verbosity import VERBOSITY_SOME, VERBOSITY_LOTS, verbosity_to_log_level


logger = logging.getLogger(__name__)


LEGACY_CONFIG_PATH = '/etc/borgmatic/config'


def parse_arguments(*arguments):
    '''
    Given command-line arguments with which this script was invoked, parse the arguments and return
    them as an ArgumentParser instance.
    '''
    parser = ArgumentParser(
        description=
            '''
            A simple wrapper script for the Borg backup software that creates and prunes backups.
            If none of the --prune, --create, or --check options are given, then borgmatic defaults
            to all three: prune, create, and check archives.
            '''
    )
    parser.add_argument(
        '-c', '--config',
        nargs='+',
        dest='config_paths',
        default=collect.DEFAULT_CONFIG_PATHS,
        help='Configuration filenames or directories, defaults to: {}'.format(' '.join(collect.DEFAULT_CONFIG_PATHS)),
    )
    parser.add_argument(
        '--excludes',
        dest='excludes_filename',
        help='Deprecated in favor of exclude_patterns within configuration',
    )
    parser.add_argument(
        '-p', '--prune',
        dest='prune',
        action='store_true',
        help='Prune archives according to the retention policy',
    )
    parser.add_argument(
        '-C', '--create',
        dest='create',
        action='store_true',
        help='Create archives (actually perform backups)',
    )
    parser.add_argument(
        '-k', '--check',
        dest='check',
        action='store_true',
        help='Check archives for consistency',
    )
    parser.add_argument(
        '-l', '--list',
        dest='list',
        action='store_true',
        help='List archives',
    )
    parser.add_argument(
        '-i', '--info',
        dest='info',
        action='store_true',
        help='Display summary information on archives',
    )
    parser.add_argument(
        '-n', '--dry-run',
        dest='dry_run',
        action='store_true',
        help='Go through the motions, but do not actually write to any repositories',
    )
    parser.add_argument(
        '-v', '--verbosity',
        type=int,
        help='Display verbose progress (1 for some, 2 for lots)',
    )

    args = parser.parse_args(arguments)

    # If any of the action flags are explicitly requested, leave them as-is. Otherwise, assume
    # defaults: Mutate the given arguments to enable the default actions.
    if args.prune or args.create or args.check or args.list or args.info:
        return args

    args.prune = True
    args.create = True
    args.check = True
    return args


def run_configuration(config_filename, args):  # pragma: no cover
    '''
    Parse a single configuration file, and execute its defined pruning, backups, and/or consistency
    checks.
    '''
    logger.info('{}: Parsing configuration file'.format(config_filename))
    config = validate.parse_configuration(config_filename, validate.schema_filename())
    (location, storage, retention, consistency, hooks) = (
        config.get(section_name, {})
        for section_name in ('location', 'storage', 'retention', 'consistency', 'hooks')
    )

    try:
        local_path = location.get('local_path', 'borg')
        remote_path = location.get('remote_path')
        borg_create.initialize_environment(storage)
        hook.execute_hook(hooks.get('before_backup'), config_filename, 'pre-backup')

        for unexpanded_repository in location['repositories']:
            repository = os.path.expanduser(unexpanded_repository)
            dry_run_label = ' (dry run; not making any changes)' if args.dry_run else ''
            if args.prune:
                logger.info('{}: Pruning archives{}'.format(repository, dry_run_label))
                borg_prune.prune_archives(
                    args.verbosity,
                    args.dry_run,
                    repository,
                    storage,
                    retention,
                    local_path=local_path,
                    remote_path=remote_path,
                )
            if args.create:
                logger.info('{}: Creating archive{}'.format(repository, dry_run_label))
                borg_create.create_archive(
                    args.verbosity,
                    args.dry_run,
                    repository,
                    location,
                    storage,
                    local_path=local_path,
                    remote_path=remote_path,
                )
            if args.check:
                logger.info('{}: Running consistency checks'.format(repository))
                borg_check.check_archives(
                   args.verbosity,
                    repository,
                    storage,
                    consistency,
                    local_path=local_path,
                    remote_path=remote_path,
                )
            if args.list:
                logger.info('{}: Listing archives'.format(repository))
                borg_list.list_archives(
                    args.verbosity,
                    repository,
                    storage,
                    local_path=local_path,
                    remote_path=remote_path,
                )
            if args.info:
                logger.info('{}: Displaying summary info for archives'.format(repository))
                borg_info.display_archives_info(
                    args.verbosity,
                    repository,
                    storage,
                    local_path=local_path,
                    remote_path=remote_path,
                )

        hook.execute_hook(hooks.get('after_backup'), config_filename, 'post-backup')
    except (OSError, CalledProcessError):
        hook.execute_hook(hooks.get('on_error'), config_filename, 'on-error')
        raise


def main():  # pragma: no cover
    try:
        configure_signals()
        args = parse_arguments(*sys.argv[1:])
        logging.basicConfig(level=verbosity_to_log_level(args.verbosity), format='%(message)s')

        config_filenames = tuple(collect.collect_config_filenames(args.config_paths))
        logger.debug('Ensuring legacy configuration is upgraded')
        convert.guard_configuration_upgraded(LEGACY_CONFIG_PATH, config_filenames)

        if len(config_filenames) == 0:
            raise ValueError('Error: No configuration files found in: {}'.format(' '.join(args.config_paths)))

        for config_filename in config_filenames:
            run_configuration(config_filename, args)
    except (ValueError, OSError, CalledProcessError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
