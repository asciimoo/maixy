from ..tmux import run


class Navigator:
    def current_window(self):
        from .macos_windows import Accessibility
        if not hasattr(self, '_windows'):
            self._windows = Accessibility()
        return self._windows.current_window()

    def restore_window(self, window):
        self._windows.restore_window(window)

    def focus(self, pane, foreground=True):
        host = pane.get('host', 'Terminal')
        if host in ('Terminal', 'iTerm') and pane.get('pane_tty'):
            if host == 'Terminal':
                script = '''on run argv
                  set found to false
                  tell application "Terminal"
                    repeat with w in windows
                      repeat with t in tabs of w
                        if tty of t is item 1 of argv then
                          set selected tab of w to t
                          if item 2 of argv is "true" then set index of w to 1
                          set found to true
                        end if
                      end repeat
                    end repeat
                    if found and item 2 of argv is "true" then activate
                  end tell
                  if not found then error "Agent terminal tab was not found"
                end run'''
            else:
                script = '''on run argv
                  set found to false
                  tell application "iTerm2"
                    repeat with w in windows
                      repeat with t in tabs of w
                        repeat with s in sessions of t
                          if tty of s is item 1 of argv then
                            select s
                            select t
                            if item 2 of argv is "true" then select w
                            set found to true
                          end if
                        end repeat
                      end repeat
                    end repeat
                    if found and item 2 of argv is "true" then activate
                  end tell
                  if not found then error "Agent iTerm session was not found"
                end run'''
            run(['osascript', '-e', script, pane['pane_tty'], str(foreground).lower()])
            return
        if pane.get('editor_bridge'):
            if not foreground:
                return
            # Select the already-open project window; do not open a new workspace.
            script = '''on run argv
              tell application "System Events"
                set p to first application process whose unix id is (item 1 of argv as integer)
                set frontmost of p to true
                if item 2 of argv is not "" then
                  repeat with w in windows of p
                    if name of w contains item 2 of argv then
                      perform action "AXRaise" of w
                      return
                    end if
                  end repeat
                end if
              end tell
            end run'''
            run(['osascript', '-e', script, str(pane['host_pid']), pane.get('window_title', '')])
            return
        raise RuntimeError('Exact navigation for ' + host + ' requires an editor bridge or MAIXY_NAVIGATION_COMMAND')
