import os.path
import sh

from visidata import *
from visidata.namedlist import namedlist

option('vgit_logfile', '', 'file to log all git commands run by vgit')

GitCmd = namedlist('GitCmd', 'sheet command output'.split())

class GitCmdLog(Sheet):
    rowtype = 'git commands'  # rowdef: GitCmd
    columns = [
        ColumnAttr('sheet'),
        ColumnAttr('command'),
        ColumnAttr('output'),
    ]
    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)
        self.rows = []

GitCmdLog.addCommand(ENTER, 'dive-row', 'vd.push(TextSheet(cursorRow[0], cursorRow[1]))', 'view output of this command'),

@VisiData.cached_property
def gitcmdlog(vd):
    return GitCmdLog('gitcmdlog')

def loggit(*args, **kwargs):
    output = maybeloggit(*args, **kwargs)

    cmdstr = 'git ' + ' '.join(args)
    vd.gitcmdlog.addRow(GitCmd([vd.sheet, cmdstr, output]))
    return output

def maybeloggit(*args, **kwargs):
    if options.vgit_logfile:
        cmdstr = 'git ' + ' '.join(args)
        with open(options.vgit_logfile, 'a') as fp:
            fp.write(cmdstr + '\n')

    return sh.git(*args, **kwargs)

def git_all(*args, git=loggit, **kwargs):
    'Return entire output of git command.'

    try:
        cmd = git('--no-pager', *args, _err_to_out=True, _decode_errors='replace', **kwargs)
        out = cmd.stdout
    except sh.ErrorReturnCode as e:
        status('git '+' '.join(args), 'error=%s' % e.exit_code)
        out = e.stdout

    out = out.decode('utf-8')

    return out

def git_lines(*args, git=loggit, **kwargs):
    'Generator of stdout lines from given git command'
    err = io.StringIO()
    try:
        for line in git('--no-pager', _err=err, *args, _decode_errors='replace', _iter=True, _bg_exc=False, **kwargs):
            yield line[:-1]  # remove EOL
    except sh.ErrorReturnCode as e:
        status('git '+' '.join(args), 'error=%s' % e.exit_code)

    errlines = err.getvalue().splitlines()
    if len(errlines) < 3:
        for line in errlines:
            status('stderr: '+line)
    else:
        vd.push(TextSheet('git ' + ' '.join(args), errlines))


def git_iter(*args, git=loggit, sep='\0', **kwargs):
    'Generator of chunks of stdout from given git command, delineated by sep character'
    bufsize = 512
    err = io.StringIO()

    chunks = []
    try:
      for data in git('--no-pager', *args, _decode_errors='replace', _out_bufsize=bufsize, _iter=True, _err=err, **kwargs):
        while True:
            i = data.find(sep)
            if i < 0:
                break
            chunks.append(data[:i])
            data = data[i+1:]
            yield ''.join(chunks)
            chunks.clear()

        chunks.append(data)
    except sh.ErrorReturnCode as e:
        errlines = err.getvalue().splitlines()
        if len(errlines) < 3:
            for line in errlines:
                status(line)
        else:
            vd.push(TextSheet('git ' + ' '.join(args), errlines))

        error('git '+' '.join(args), 'error=%s' % e.exit_code)

    r = ''.join(chunks)
    if r:
        yield r

    errlines = err.getvalue().splitlines()
    if len(errlines) < 3:
        for line in errlines:
            status(line)
    else:
        vd.push(TextSheet('git ' + ' '.join(args), errlines))


class GitFile:
    def __init__(self, path, gitsrc):
        self.path = path
        self.filename = os.path.relpath(path.abspath(), gitsrc.abspath())
        self.is_dir = self.path.is_dir()

    def __str__(self):
        return self.filename + (self.is_dir and '/' or '')


class GitSheet(Sheet):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.extra_args = []

    @property
    def worktree(self):
        if isinstance(self.source, GitSheet):
            return self.source.worktree
        elif isinstance(self.source, Path):
            return self.source

    def _git_args(self):
        return [
            '--git-dir', self.worktree.joinpath('.git').abspath(),
            '--work-tree', self.worktree.abspath()
        ]

    @Sheet.name.setter
    def name(self, name):
        self._name = name.strip()

    def git_iter(self, *args, **kwargs):
        yield from git_iter(*self._git_args(), *args, **kwargs)

    def git_lines(self, *args, **kwargs):
        return git_lines(*self._git_args(), *args, **kwargs)

    def git_all(self, *args, **kwargs):
        return git_all(*self._git_args(), *args, **kwargs)

    @asyncthread
    def git(self, *args, **kwargs):
        'Run git command that modifies the repo'
        args = list(args) + self.extra_args
        self.extra_args.clear()

        for line in self.git_lines(*args, **kwargs):
            status(line)

        if isinstance(self.source, GitSheet):
            self.source.reload()

        self.reload()

    @staticmethod
    def inProgress():
        if Path('.git/rebase-merge').exists() or Path('.git/rebase-apply/rebasing').exists():
            return 'rebasing'
        elif Path('.git/rebase-apply').exists():
            return 'applying'
        elif Path('.git/CHERRY_PICK_HEAD').exists():
            return 'cherry-picking'
        elif Path('.git/MERGE_HEAD').exists():
            return 'merging'
        elif Path('.git/BISECT_LOG').exists():
            return 'bisecting'
        return ''


    def abortWhatever(self):
        inp = self.inProgress()
        if inp.startswith('cherry-pick'):
            self.git('cherry-pick', '--abort')
        elif inp.startswith('merg'):
            self.git('merge', '--abort')
        elif inp.startswith('bisect'):
            self.git('bisect', 'reset')
        elif inp.startswith('rebas') or inp.startswith('apply'):
            self.git('rebase', '--abort')  # or --quit?
        else:
            status('nothing to abort')

    @property
    def rootSheet(self):
        if isinstance(self.source, GitSheet):
            return self.source.rootSheet
        return self

    def leftStatus(self):
        inp = self.inProgress()
        ret = ('[%s] ' % inp) if inp else ''
        if hasattr(self.rootSheet, 'branch'):
            ret += '‹%s%s› ' % (self.rootSheet.branch, self.rootSheet.remotediff)

        return ret + super().leftStatus()

    def git_apply(self, hunk, *args):
        self.git("apply", "-p0", "-", *args, _in="\n".join(hunk[7]) + "\n")
        status('applied hunk (lines %s-%s)' % (hunk[3], hunk[3]+hunk[4]))

GitSheet.addCommand('f', 'git-force', 'extra_args.append("--force"); status("--force next git command")', 'add --force to next git command')


# cached by GitStatus sheets
FileStatus = namedlist('FileStatus', 'status adds dels'.split())

class GitStatus(GitSheet):
    rowtype = 'files'  # rowdef: GitFile
    colorizers = [
        CellColorizer(3, 'green',   lambda s,c,r,v: r and c and c.name == 'staged' and s.git_status(r).status[0] == 'M'), # staged mod
        CellColorizer(1, 'red',     lambda s,c,r,v: r and c and c.name == 'staged' and s.git_status(r).status == 'D '), # staged delete
        RowColorizer(1, 'magenta',  lambda s,c,r,v: r and s.git_status(r).status in ['A ', 'M ']), # staged add/mod
        RowColorizer(1, '88',       lambda s,c,r,v: r and s.git_status(r).status[1] == 'D'), # unstaged delete
        RowColorizer(1, '237 blue', lambda s,c,r,v: r and s.git_status(r).status == '!!'),  # ignored
        RowColorizer(1, '237 blue', lambda s,c,r,v: r and s.git_status(r).status == '??'),  # untracked
    ]
    columns = [
        Column('path', getter=lambda c,r: str(r)),
        Column('status', getter=lambda c,r: c.sheet.statusText(c.sheet.git_status(r)), width=8),
        Column('status_raw', getter=lambda c,r: c.sheet.git_status(r), width=0),
        Column('staged', getter=lambda c,r: c.sheet.git_status(r).dels),
        Column('unstaged', getter=lambda c,r: c.sheet.git_status(r).adds),
        Column('type', getter=lambda c,r: r.is_dir and '/' or r.path.suffix, width=0),
        Column('size', type=int, getter=lambda c,r: r.path.filesize),
        Column('mtime', type=date, getter=lambda c,r: r.path.mtime),
    ]
    nKeys = 1

    def __init__(self, p):
        super().__init__('/'.join(Path(p.abspath()).parts[-2:]), source=p)
        self.branch = ''
        self.remotediff = ''  # ahead/behind status

        self._cachedStatus = {}  # [filename] -> FileStatus(['!!' or '??' status, adds, dels])

    def statusText(self, st):
        vmod = {'A': 'add', 'D': 'rm', 'M': 'mod', 'T': 'chmod', '?': 'out', '!': 'ignored', 'U': 'unmerged'}
        x, y = st.status
        if st == '??': # untracked
            return 'new'
        elif st == '!!':  # ignored
            return 'ignored'
        elif x != ' ' and y == ' ': # staged
            return vmod.get(x, x)
        elif y != ' ': # unstaged
            return vmod.get(y, y)
        else:
            return ''

    @property
    def workdir(self):
        return str(self.source)

    def git_status(self, r):
        '''return tuple of (status, adds, dels).
        status like !! ??
        adds and dels are lists of additions and deletions.
        '''
        if not r:
            return None
        ret = self._cachedStatus.get(r.filename, None)
        if not ret:
            ret = FileStatus(["//", None, None])
            self._cachedStatus[r.filename] = ret

        return ret

    def ignored(self, fn):
        if options.vgit_show_ignored:
            return False

        if fn in self._cachedStatus:
            return self._cachedStatus[fn].status == '!!'

        return False


    def getBranchStatuses(self):
        ret = {}  # localbranchname -> "+5/-2"
        for branch_status in self.git_lines('for-each-ref', '--format=%(refname:short) %(upstream:short) %(upstream:track)', 'refs/heads'):
            m = re.search(r'''(\S+)\s*
                              (\S+)?\s*
                              (\[
                              (ahead.(\d+)),?\s*
                              (behind.(\d+))?
                              \])?''', branch_status, re.VERBOSE)
            if not m:
                status('unmatched branch status: ' + branch_status)
                continue

            localb, remoteb, _, _, nahead, _, nbehind = m.groups()
            if nahead:
                r = '+%s' % nahead
            else:
                r = ''
            if nbehind:
                if r:
                    r += '/'
                r += '-%s' % nbehind
            ret[localb] = r

        return ret

    @asyncthread
    def reload(self):
        files = [GitFile(p, self.source) for p in self.source.iterdir() if p.name not in ('.git')]  # files in working dir

        filenames = dict((gf.filename, gf) for gf in files)
        self.branch = self.git_all('rev-parse', '--abbrev-ref', 'HEAD').strip()
        self.remotediff = self.getBranchStatuses().get(self.branch, 'no branch')

        self.rows = []
        self._cachedStatus.clear()
        for fn in self.git_iter('ls-files', '-z'):
            self._cachedStatus[fn] = FileStatus(['  ', None, None])  # status, adds, dels

        for status_line in self.git_iter('status', '-z', '-unormal', '--ignored'):
            if status_line:
                if status_line[2:3] == ' ':
                    st, fn = status_line[:2], status_line[3:]
                else:
                    fn = status_line
                    st = '//'
                gf = GitFile(self.source.joinpath(fn), self.source)
                self._cachedStatus[gf.filename] = FileStatus([st, None, None])
                if gf.filename not in filenames:
                    if not self.ignored(gf.filename):
                        self.addRow(gf)

        for line in self.git_iter('diff-files', '--numstat', '-z'):
            if not line: continue
            adds, dels, fn = line.split('\t')
            if fn not in self._cachedStatus:
                self._cachedStatus[fn] = FileStatus(['##', None, None])
            cs = self._cachedStatus[fn]
            cs.adds = '+%s/-%s' % (adds, dels)

        for line in self.git_iter('diff-index', '--cached', '--numstat', '-z', 'HEAD'):
            if not line: continue
            adds, dels, fn = line.split('\t')
            if fn not in self._cachedStatus:
                self._cachedStatus[fn] = FileStatus(['$$', None, None])
            cs = self._cachedStatus[fn]
            cs.dels = '+%s/-%s' % (adds, dels)

        for fn, gf in filenames.items():
            if not self.ignored(gf.filename):
                self.addRow(gf)

        self.orderBy(None, self.columns[-1], reverse=True)

        self.recalc()  # erase column caches

GitStatus.addCommand('a', 'git-add', 'git("add", cursorRow.filename)', 'add this new file or modified file to staging'),
GitStatus.addCommand('m', 'git-mv', 'git("mv", cursorRow.filename, input("rename file to: ", value=cursorRow.filename))', 'rename this file'),
GitStatus.addCommand('d', 'git-rm', 'git("rm", cursorRow.filename)', 'stage this file for deletion'),
GitStatus.addCommand('r', 'git-reset', 'git("reset", "HEAD", cursorRow.filename)', 'reset/unstage this file'),
GitStatus.addCommand('c', 'git-checkout', 'git("checkout", cursorRow.filename)', 'checkout this file'),
GitStatus.addCommand('ga', 'git-add-selected', 'git("add", *[r.filename for r in selectedRows])', 'add all selected files to staging'),
GitStatus.addCommand('gd', 'git-rm-selected', 'git("rm", *[r.filename for r in selectedRows])', 'delete all selected files'),
GitStatus.addCommand(None, 'git-commit', 'git("commit", "-m", input("commit message: "))', 'commit changes'),
GitStatus.addCommand('V', 'open-file', 'vd.push(TextSheet(cursorRow.filename, Path(cursorRow.filename)))', 'open file'),
GitSheet.addCommand(None, 'ignore-file', 'open(workdir+"/.gitignore", "a").write(cursorRow.filename+"\\n"); reload()', 'add file to toplevel .gitignore'),
GitSheet.addCommand(None, 'ignore-wildcard', 'open(workdir+"/.gitignore", "a").write(input("add wildcard to .gitignore: "))', 'add input line to toplevel .gitignore'),


@GitStatus.api
def dive_rows(sheet, *gitfiles):
    if len(gitfiles) == 1:
        gf = gitfiles[0]
        if gf.is_dir:
            vs = GitStatus(gf.path)
        else:
            vs = DifferSheet(gf, "HEAD", "index", "working", source=sheet)
    else:
        vs = getHunksSheet(sheet, *gitfiles)
    vd.push(vs)

GitStatus.addCommand(ENTER, 'dive-row', 'sheet.dive_rows(cursorRow)', 'push unstaged diffs for this file or dive into directory'),
GitStatus.addCommand('g'+ENTER, 'dive-rows', 'sheet.dive_rows(*(selectedRows or rows))', 'push unstaged diffs for selected files or all files'),


globalCommand('g/', 'git-grep', 'vd.push(GitGrep(input("git grep: ")))', 'find in all files'),
Sheet.unbindkey('g/')

GitSheet.addCommand('z^J', 'diff-file-staged', 'vd.push(getStagedHunksSheet(sheet, cursorRow))', 'push staged diffs for this file'),
GitSheet.addCommand('gz^J', 'diff-selected-staged', 'vd.push(getStagedHunksSheet(sheet, *(selectedRows or rows)))', 'push staged diffs for selected files or all files'),

GitSheet.addCommand('gD', 'git-output', 'vd.push(vd.gitcmdlog)', 'show output of git commands this session')

globalCommand('gi', 'git-exec', 'sheet.git_exec(input("gi", type="git"))')

@GitSheet.api
def git_exec(sheet, cmdstr):
    vd.push(TextSheet(cmdstr, sheet.git_lines(*cmdstr.split())))


#GitSheet.addCommand('2', 'vd.push(GitMerge(cursorRow))', 'push merge for this file'),
GitSheet.addCommand('L', 'git-blame', 'vd.push(GitBlame(cursorRow))', 'push blame for this file'),

options.set('disp_note_none', '', GitSheet)
