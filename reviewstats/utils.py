## -*- coding: utf-8 -*-
# Copyright (C) 2011 - Soren Hansen
# Copyright (C) 2013 - Red Hat, Inc.
#

# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
"""Utility functions module
"""

import glob
import json
import logging
import os
from six.moves import cPickle as pickle
import time

import paramiko

CACHE_AGE = 3600  # Seconds

LOG = logging.getLogger(__name__)


def get_projects_info(project=None, all_projects=False, base_dir='./projects'):
    """Return the list of project dict objects.

    :param project: pathname of the JSON project info file.
    :type project: str or None
    :param bool all_projects:
        If True deserialize all the “.json“ files of officials projects in
        base_dir.
    :param str base_dir:
        dirname of the path containing the json projects files.
    :return: unserialized json of project info.
    :rtype: dict

    project or all_projects=True must be given.

    .. note::
        Unofficial projects are filtered out if all_projects is True.
        Official qualification is a key of the json file name “unofficial”, if
        present and true, its an unofficial project.

    .. note::
        all_projects parameters overrides project parameter value if True
    """
    if all_projects:
        files = glob.glob('%s/*.json' % base_dir)
    else:
        files = [project]

    projects = []

    for fn in files:
        if os.path.isfile(fn):
            with open(fn, 'r') as f:
                try:
                    project = json.loads(f.read())
                except Exception:
                    LOG.error('Failed to parse %s' % fn)
                    raise
                if not (all_projects and project.get('unofficial')):
                    projects.append(project)

    return projects


def projects_q(project):
    """Return the gerrit query selecting all the project in the given list

    :param project: List of gerrit project name.
    :type project: list of str
    :return:
        gerrit query according to `Searching Changes`_ section of gerrit
        documentation.
    :rtype: str

    .. _Searching Changes:
        https://review.openstack.org/Documentation/user-search.html
    """
    return ('(' +
            ' OR '.join(['project:' + p for p in project['subprojects']]) +
            ')')


def get_changes(projects, ssh_user, ssh_key, only_open=False, stable='',
                server='review.openstack.org'):
    """Get the changesets data list.

    :param projects: List of gerrit project names.
    :type projects: list of str
    :param str ssh_user: Gerrit username.
    :param str ssh_key: Filename of one SSH key registered at gerrit.
    :param bool only_open: If True, get only the not closed reviews.
    :param str stable:
        Name of the stable branch. If empty string, the changesets are not
        filtered by any branch.

    :return: List of de-serialized JSON changeset data as returned by gerrit.
    :rtype: list

    .. note::
        If all the issue are requested whatever the branch is, a cache system
        override the gerrit request.
        Requesting only the open changes isn't nearly as big of a deal,
        so just get the current data.
        Also do not use cache for stable stats as they cover different
        results.
        Cached results are pickled per project in the following filenames:
        “.{projectname}-changes.pickle”.
    """
    all_changes = []

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    for project in projects:
        changes = []
        logging.debug('Getting changes for project %s' % project['name'])

        if not only_open and not stable:
            # Only use the cache for *all* changes (the entire history).
            pickle_fn = '.%s-changes.pickle' % project['name']

            if os.path.isfile(pickle_fn):
                mtime = os.stat(pickle_fn).st_mtime
                if (time.time() - mtime) <= CACHE_AGE:
                    with open(pickle_fn, 'r') as f:
                        try:
                            changes = pickle.load(f)
                        except MemoryError:
                            changes = None

        if not changes:
            while True:
                connect_attempts = 3
                for attempt in range(connect_attempts):
                    try:
                        client.connect(server, port=29418,
                                       key_filename=ssh_key,
                                       username=ssh_user)
                    except paramiko.SSHException:
                        try:
                            client.connect(server, port=29418,
                                           key_filename=ssh_key,
                                           username=ssh_user,
                                           allow_agent=False)
                        except paramiko.SSHException:
                            if attempt == connect_attempts + 1:
                                raise
                            time.sleep(3)
                            continue
                    # Connected successfully
                    break

                cmd = ('gerrit query %s --all-approvals --patch-sets '
                       '--format JSON' % projects_q(project))
                if only_open:
                    cmd += ' status:open'
                if stable:
                    cmd += ' branch:stable/%s' % stable
                if changes:
                    cmd += ' resume_sortkey:%s' % changes[-2]['sortKey']
                stdin, stdout, stderr = client.exec_command(cmd)
                for l in stdout:
                    changes += [json.loads(l)]
                if changes[-1]['rowCount'] == 0:
                    break

            if not only_open and not stable:
                with open(pickle_fn, 'w') as f:
                    pickle.dump(changes, f)

        all_changes.extend(changes)

    return all_changes


def patch_set_approved(patch_set):
    """Return True if the patchset has been approved.

    :param dict patch_set: De-serialized dict of a gerrit change

    :return: True if one of the patchset reviews approved it.
    :rtype: bool
    """
    approvals = patch_set.get('approvals', [])
    for review in approvals:
        if (review['type'] == 'Approved' or
                (review['type'] == 'Workflow' and int(review['value']) > 0)):
            return True
    return False


def is_workinprogress(change):
    """Return True if the patchset is WIP

    :param dict change: De-serialized dict of a gerrit change

    :return: True if one of the votes on the review sets it to WIP.
    """
    # This indicates WIP for older Gerrit versions
    if change['status'] != 'NEW':
        return True

    # Gerrit 2.8 WIP
    last_patch = change['patchSets'][-1]
    try:
        approvals = last_patch['approvals']
    except KeyError:
        # Means no one has voted on the latest patch set yet
        return False
    for a in approvals:
        if a['type'] == 'Workflow' and int(a['value']) < 0:
            return True

    return False


def get_age_of_patch(patch, now_ts):
    """Compute the number of seconds since the patch submission and now.

    :param dict patch: Deserialized JSON of a Gerrit patchset
    :param int now_ts: Unix-like timestamp in seconds since EPOCH.
    :return int: number of seconds between patch submission and now_ts

    .. note::
        The createdOn timestamp on the patch isn't what we want.
        It's when the patch was written, not submitted for review.
        The next best thing in the data we have is the time of the
        first review.  When all is working well, jenkins or smokestack
        will comment within the first hour or two, so that's better
        than the other timestamp, which may reflect that the code
        was written many weeks ago, even though it was just recently
        submitted for review.
    """
    approvals = patch.get('approvals', [])
    approvals.sort(key=lambda a: a['grantedOn'])
    if approvals:
        return now_ts - approvals[0]['grantedOn']
    else:
        return now_ts - patch['createdOn']
