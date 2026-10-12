export interface EditorCommand<TContext> {
  readonly label: string;
  execute(context: TContext): void;
  undo(context: TContext): void;
}

export interface CommandHistoryState {
  canUndo: boolean;
  canRedo: boolean;
  undoLabel?: string;
  redoLabel?: string;
}

export class CommandHistory<TContext> {
  private readonly undoStack: EditorCommand<TContext>[] = [];
  private readonly redoStack: EditorCommand<TContext>[] = [];

  constructor(private readonly limit = 200) {
    if (!Number.isInteger(limit) || limit < 1) {
      throw new Error('Command history limit must be a positive integer.');
    }
  }

  get state(): CommandHistoryState {
    return {
      canUndo: this.undoStack.length > 0,
      canRedo: this.redoStack.length > 0,
      undoLabel: this.undoStack.at(-1)?.label,
      redoLabel: this.redoStack.at(-1)?.label,
    };
  }

  execute(context: TContext, command: EditorCommand<TContext>): void {
    command.execute(context);
    this.undoStack.push(command);
    if (this.undoStack.length > this.limit) this.undoStack.shift();
    this.redoStack.length = 0;
  }

  undo(context: TContext): boolean {
    const command = this.undoStack.pop();
    if (!command) return false;
    command.undo(context);
    this.redoStack.push(command);
    return true;
  }

  redo(context: TContext): boolean {
    const command = this.redoStack.pop();
    if (!command) return false;
    command.execute(context);
    this.undoStack.push(command);
    return true;
  }

  clear(): void {
    this.undoStack.length = 0;
    this.redoStack.length = 0;
  }
}
