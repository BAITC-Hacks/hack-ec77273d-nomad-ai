from loaders import load_dataset

if __name__ == '__main__':
    data = load_dataset()
    print(f'Loaded {len(data.employees)} employees, {len(data.events)} events, {len(data.history)} activities')
    print(next(iter(data.employees.values())).model_dump_json(indent=2))
