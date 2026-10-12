import { describe, expect, it } from 'vitest';
import { CommandHistory, type EditorCommand } from './command-history';

describe('CommandHistory', () => {
  it('executes, undoes, and redoes commands in order', () => {
    const context = { value: 0 };
    const history = new CommandHistory<typeof context>();
    const add = (amount: number): EditorCommand<typeof context> => ({
      label: `Add ${amount}`,
      execute: (target) => { target.value += amount; },
      undo: (target) => { target.value -= amount; },
    });

    history.execute(context, add(3));
    history.execute(context, add(4));
    expect(context.value).toBe(7);
    expect(history.state).toMatchObject({ canUndo: true, canRedo: false, undoLabel: 'Add 4' });

    expect(history.undo(context)).toBe(true);
    expect(context.value).toBe(3);
    expect(history.state.redoLabel).toBe('Add 4');

    expect(history.redo(context)).toBe(true);
    expect(context.value).toBe(7);
  });

  it('drops the redo branch when a new command is executed', () => {
    const context = { value: 0 };
    const history = new CommandHistory<typeof context>();
    const command: EditorCommand<typeof context> = {
      label: 'Increment',
      execute: (target) => { target.value += 1; },
      undo: (target) => { target.value -= 1; },
    };

    history.execute(context, command);
    history.undo(context);
    history.execute(context, command);

    expect(history.state.canRedo).toBe(false);
  });
});
