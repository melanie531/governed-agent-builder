"""Summary of current caller model projections, never an authorization decision."""


def listed_model(item):
    return item.get('kind') == 'model' and not item.get('parent_id') and (
        item.get('fixture') is True or item.get('recency') == 'recent')


def model_access_summary(items):
    rows = [x for x in items if listed_model(x)]
    def identity(x):
        return x.get('model_id') or x.get('record_id') or x['id']
    total = {identity(x) for x in rows}
    granted = {identity(x) for x in rows if x.get('granted') is True}
    requestable = {identity(x) for x in rows if x.get('requestable') is True} - granted
    callable_ids = {identity(x) for x in rows if x.get('granted') is True
                    and x.get('usable') is True and x.get('execution_ready') is True
                    and x.get('supported') is not False and not x.get('fixture')}
    return dict(granted=len(granted), requestable=len(requestable),
                callable=len(callable_ids), available=len(total))
