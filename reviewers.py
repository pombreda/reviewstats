#!/usr/bin/env python
#
# Copyright (C) 2011 - Soren Hansen
# Copyright (C) 2013 - Red Hat, Inc.

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


import calendar
import datetime
import getpass
import optparse
import prettytable
import sys

import utils


def round_to_day(ts):
    SECONDS_PER_DAY = 60*60*24
    return (ts / (SECONDS_PER_DAY)) * SECONDS_PER_DAY


def process_patchset(project, patchset, reviewers, ts):
    latest_core_neg_vote = 0
    latest_core_pos_vote = 0
    for review in patchset.get('approvals', []):
        if review['type'] != 'CRVW':
            # Only count code reviews.  Don't add another for Approved, which
            # is type 'APRV'
            continue
        if review['by'].get('username', 'unknown') not in project['core-team']:
            # Only checking for disagreements from core team members
            continue
        if int(review['value']) > 0:
            latest_core_pos_vote = max(latest_core_pos_vote,
                                       int(review['grantedOn']))
        else:
            latest_core_neg_vote = max(latest_core_neg_vote,
                                       int(review['grantedOn']))

    for review in patchset.get('approvals', []):
        if review['grantedOn'] < ts:
            continue

        if review['type'] != 'CRVW':
            # Only count code reviews.  Don't add another for Approved, which
            # is type 'APRV'
            continue

        reviewer = review['by'].get('username', 'unknown')
        reviewers.setdefault(reviewer,
                             {'votes': {'-2': 0, '-1': 0, '1': 0, '2': 0}})
        reviewers[reviewer].setdefault('disagreements', 0)
        reviewers[reviewer]['total'] = reviewers[reviewer].get('total', 0) + 1
        cur = reviewers[reviewer]['votes'][review['value']]
        reviewers[reviewer]['votes'][review['value']] = cur + 1
        if (review['value'] in ('1', '2')
                and int(review['grantedOn']) < latest_core_neg_vote):
            # A core team member gave a negative vote after this person gave a
            # positive one
            cur = reviewers[reviewer]['disagreements']
            reviewers[reviewer]['disagreements'] = cur + 1
        if (review['value'] in ('-1', '-2')
                and int(review['grantedOn']) < latest_core_pos_vote):
            # A core team member gave a positive vote after this person gave a
            # negative one
            cur = reviewers[reviewer]['disagreements']
            reviewers[reviewer]['disagreements'] = cur + 1


def main(argv=None):
    if argv is None:
        argv = sys.argv

    optparser = optparse.OptionParser()
    optparser.add_option(
        '-p', '--project', default='projects/nova.json',
        help='JSON file describing the project to generate stats for')
    optparser.add_option(
        '-a', '--all', action='store_true',
        help='Generate stats across all known projects (*.json)')
    optparser.add_option(
        '-d', '--days', type='int', default=14,
        help='Number of days to consider')
    optparser.add_option(
        '-u', '--user', default=getpass.getuser(), help='gerrit user')
    optparser.add_option(
        '-k', '--key', default=None, help='ssh key for gerrit')

    options, args = optparser.parse_args()

    projects = utils.get_projects_info(options.project, options.all)

    if not projects:
        print "Please specify a project."
        sys.exit(1)

    reviewers = {}

    cut_off = datetime.datetime.now() - datetime.timedelta(days=options.days)
    ts = calendar.timegm(cut_off.timetuple())

    for project in projects:
        changes = utils.get_changes([project], options.user, options.key)
        for change in changes:
            for patchset in change.get('patchSets', []):
                process_patchset(project, patchset, reviewers, ts)

    reviewers = [(v, k) for k, v in reviewers.iteritems()
                 if k.lower() not in ('jenkins', 'smokestack')]
    reviewers.sort(reverse=True, key=lambda r: r[0]['total'])

    if options.all:
        print 'Reviews for the last %d days in projects: %s' \
            % (options.days, [project['name'] for project in projects])
    else:
        print 'Reviews for the last %d days in %s' \
            % (options.days, projects[0]['name'])
    if options.all:
        print '** -- Member of at least one core reviewer team'
    else:
        print '** -- %s-core team member' % projects[0]['name']
    # Do logical processing of reviewers.
    reviewer_data = []
    total = 0
    for k, v in reviewers:
        in_core_team = False
        for project in projects:
            if v in project['core-team']:
                in_core_team = True
                break
        name = '%s%s' % (v, ' **' if in_core_team else '')
        plus = float(k['votes']['2'] + k['votes']['1'])
        minus = float(k['votes']['-2'] + k['votes']['-1'])
        ratio = (plus / (plus + minus)) * 100
        r = (k['total'], k['votes']['-2'],
            k['votes']['-1'], k['votes']['1'],
            k['votes']['2'], "%5.1f%%" % ratio)
        dratio = ((float(k['disagreements']) / plus) * 100) if plus else 0.0
        d = (k['disagreements'], "%5.1f%%" % dratio)
        reviewer_data.append((name, r, d))
        total += k['total']
    # And output.
    table = prettytable.PrettyTable(
        ('Reviewer',
         'Reviews   -2  -1  +1  +2    +/- %',
         'Disagreements*'))
    for (name, r_data, d_data) in reviewer_data:
        r = '%7d  %3d %3d %3d %3d   %s' % r_data
        d = '%3d (%s)' % d_data
        table.add_row((name, r, d))
    print table
    print '\nTotal reviews: %d' % total
    print 'Total reviewers: %d' % len(reviewers)
    print '\n(*) Disagreements are defined as a +1 or +2 vote on a patch ' \
          'where a core team member later gave a -1 or -2 vote, or a ' \
          'negative vote overridden with a postive one afterwards.'

    return 0


if __name__ == '__main__':
    sys.exit(main())
