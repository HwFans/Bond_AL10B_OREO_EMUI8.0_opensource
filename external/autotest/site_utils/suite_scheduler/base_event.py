# Copyright (c) 2012 The Chromium OS Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import logging

import common
from autotest_lib.client.common_lib import priorities
from autotest_lib.client.common_lib.cros import dev_server
from autotest_lib.server import utils


"""Module containing base class and methods for working with scheduler events.

@var _SECTION_SUFFIX: suffix of config file sections that apply to derived
                      classes of TimedEvent.
"""


_SECTION_SUFFIX = '_params'

# Pattern of latest Launch Control build for a branch and a target.
_LATEST_LAUNCH_CONTROL_BUILD_FMT = '%s/%s/LATEST'

def SectionName(keyword):
    """Generate a section name for a *Event config stanza.

    @param keyword: Name of the event, e.g., nightly, weekly etc.
    """
    return keyword + _SECTION_SUFFIX


def HonoredSection(section):
    """Returns True if section is something _ParseConfig() might consume.

    @param section: Name of the config section.
    """
    return section.endswith(_SECTION_SUFFIX)


def BuildName(board, type, milestone, manifest):
    """Format a build name, given board, type, milestone, and manifest number.

    @param board: board the manifest is for, e.g. x86-alex.
    @param type: one of 'release', 'factory', or 'firmware'
    @param milestone: (numeric) milestone the manifest was associated with.
    @param manifest: manifest number, e.g. '2015.0.0'
    @return a build name, e.g. 'x86-alex-release/R20-2015.0.0'
    """
    return '%s-%s/R%s-%s' % (board, type, milestone, manifest)


class BaseEvent(object):
    """Represents a supported scheduler event.

    @var PRIORITY: The priority of suites kicked off by this event.
    @var TIMEOUT: The max lifetime of suites kicked off by this event.

    @var _keyword: the keyword/name of this event, e.g. new_build, nightly.
    @var _mv: ManifestVersions instance used to query for new builds, etc.
    @var _always_handle: whether to make ShouldHandle always return True.
    @var _tasks: set of Task instances that run on this event.
                 Use a set so that instances that encode logically equivalent
                 Tasks get de-duped before we even try to schedule them.
    """


    PRIORITY = priorities.Priority.DEFAULT
    TIMEOUT = 24  # Hours


    @classmethod
    def CreateFromConfig(cls, config, manifest_versions):
        """Instantiate a cls object, options from |config|.

        @param config: A ForgivingConfigParser instance.
        @param manifest_versions: ManifestVersions instance used to query for
                new builds, etc.
        """
        return cls(manifest_versions, **cls._ParseConfig(config))


    @classmethod
    def _ParseConfig(cls, config):
        """Parse config and return a dict of parameters for this event.

        Uses cls.KEYWORD to determine which section to look at, and parses
        the following options:
          always_handle: If True, ShouldHandle() must always return True.

        @param config: a ForgivingConfigParser instance.
        """
        section = SectionName(cls.KEYWORD)
        return {'always_handle': config.getboolean(section, 'always_handle')}


    def __init__(self, keyword, manifest_versions, always_handle):
        """Constructor.

        @param keyword: the keyword/name of this event, e.g. nightly.
        @param manifest_versions: ManifestVersions instance to use for querying.
        @param always_handle: If True, make ShouldHandle() always return True.
        """
        self._keyword = keyword
        self._mv = manifest_versions
        self._tasks = set()
        self._always_handle = always_handle


    @property
    def keyword(self):
        """Getter for private |self._keyword| property."""
        return self._keyword


    @property
    def tasks(self):
        """Getter for private |self._tasks| property."""
        return self._tasks


    @property
    def launch_control_branches_targets(self):
        """Get a dict of branch:targets for Launch Control from all tasks.

        branch: Name of a Launch Control branch.
        targets: A list of targets for the given branch.
        """
        branches = {}
        for task in self._tasks:
            for branch in task.launch_control_branches:
                branches.setdefault(branch, []).extend(
                        task.launch_control_targets)
        return branches


    @tasks.setter
    def tasks(self, iterable_of_tasks):
        """Set the tasks property with an iterable.

        @param iterable_of_tasks: list of Task instances that can fire on this.
        """
        self._tasks = set(iterable_of_tasks)


    def Merge(self, to_merge):
        """Merge this event with to_merge, changing all mutable properties.

        keyword remains unchanged; the following take on values from to_merge:
          _tasks
          _mv
          _always_handle

        @param to_merge: A BaseEvent instance to merge into this instance.
        """
        self.tasks = to_merge.tasks
        self._mv = to_merge._mv
        self._always_handle = to_merge._always_handle


    def Prepare(self):
        """Perform any one-time setup that must occur before [Should]Handle().

        Must be implemented by subclasses.
        """
        raise NotImplementedError()


    def GetBranchBuildsForBoard(self, board):
        """Get per-branch, per-board builds since last run of this event.

        @param board: the board whose builds we want.
        @return {branch: [build-name]}

        Must be implemented by subclasses.
        """
        raise NotImplementedError()


    def GetLaunchControlBuildsForBoard(self, board):
        """Get per-branch, per-board builds since last run of this event.

        @param board: the board whose builds we want.

        @return: A list of Launch Control builds for the given board, e.g.,
                ['git_mnc_release/shamu-eng/123',
                 'git_mnc_release/shamu-eng/124'].

        Must be implemented by subclasses.
        """
        raise NotImplementedError()


    def ShouldHandle(self):
        """Returns True if this BaseEvent should be Handle()'d, False if not.

        Must be extended by subclasses.
        """
        return self._always_handle


    def UpdateCriteria(self):
        """Updates internal state used to decide if this event ShouldHandle()

        Must be implemented by subclasses.
        """
        raise NotImplementedError()


    def FilterTasks(self):
        """Filter the tasks to only return tasks should run now.

        One use case is that Nightly task can run at each hour. The override of
        this function in Nightly class will only return the tasks set to run in
        current hour.

        @return: A list of tasks can run now.
        """
        return list(self.tasks)


    def Handle(self, scheduler, branch_builds, board, force=False,
               launch_control_builds=None):
        """Runs all tasks in self._tasks that if scheduled, can be
        successfully run.

        @param scheduler: an instance of DedupingScheduler, as defined in
                          deduping_scheduler.py
        @param branch_builds: a dict mapping branch name to the build to
                              install for that branch, e.g.
                              {'R18': ['x86-alex-release/R18-1655.0.0'],
                               'R19': ['x86-alex-release/R19-2077.0.0']
                               'factory': ['x86-alex-factory/R19-2077.0.5']}
        @param board: the board against which to Run() all of self._tasks.
        @param force: Tell every Task to always Run().
        @param launch_control_builds: A list of Launch Control builds.
        """
        logging.info('Handling %s for %s', self.keyword, board)
        # we need to iterate over an immutable copy of self._tasks
        tasks = list(self.tasks) if force else self.FilterTasks()
        for task in tasks:
            if task.AvailableHosts(scheduler, board):
                if not task.Run(scheduler, branch_builds, board, force,
                                self._mv, launch_control_builds):
                    self._tasks.remove(task)
            elif task.ShouldHaveAvailableHosts():
                logging.warning('Skipping %s on %s, due to lack of hosts.',
                                task, board)


    def _LatestLaunchControlBuilds(self, board):
        """Get latest per-branch, per-board builds.

        @param board: the board whose builds we want, e.g., shamu.

        @return: A list of Launch Control builds for the given board, e.g.,
                ['git_mnc_release/shamu-eng/123',
                 'git_mnc_release/shamu-eng/124'].
        """
        # Translate board name to the actual board name in build target.
        board = utils.ANDROID_BOARD_TO_TARGET_MAP.get(board, board)
        # Pick a random devserver based on tick, this is to help load balancing
        # across all devservers.
        devserver = dev_server.AndroidBuildServer.random()
        builds = []
        for branch, targets in self.launch_control_branches_targets.items():
            # targets is a list of Launch Control targets, e.g., shamu-eng.
            # The first part should match the board name.
            match_targets = [
                    t for t in targets
                    if board == utils.parse_launch_control_target(t)[0]]
            for target in match_targets:
                latest_build = (_LATEST_LAUNCH_CONTROL_BUILD_FMT %
                                (branch, target))
                builds.append(devserver.translate(latest_build))
        return builds
