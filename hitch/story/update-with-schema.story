Updating document with a schema:
  docs: compound/update
  based on: strictyaml
  description: |
    When StrictYAML loads a document with a schema, it checks that future
    updates to that document follow the original schema.
  given:
    setup: |
      import strictyaml as s
  variations:
    GitHub \#72:
      steps:
      - Run: |-
          doc = s.load('a: 9', s.Map({
            'a': s.Str(),
            s.Optional('b'): s.Int(),
          }))
          doc['b'] = 9
          assert doc['b'] == 9

    Works on empty mapping:
      steps:
      - Run: |-
          doc = s.load('', s.EmptyDict() | s.Map({
            'a': s.Int(),
          }))
          doc['a'] = 9
          assert doc['a'] == 9, doc.as_yaml()

    Works on complex types:
      steps:
      - Run: |-
          doc = s.load('a: 8', s.Map({'a': s.Int() | s.Float()}))
          assert type(doc['a'].data) == int, repr(doc.data)
          doc['a'] = '5.'
          assert type(doc['a'].data) == float, repr(doc.data)
          assert doc['a'] == 5.

    Will not work on empty sequence:
      steps:
      - Run:
          code: |
            doc = s.load('', s.EmptyList() | s.Seq(s.Int()))
            doc[0] = 9
          raises:
            type: strictyaml.exceptions.YAMLSerializationError
            message: |-
                cannot extend list via __setitem__.  Instead, replace whole list on parent node.

    Works on map with setting, updating, and then setting multiple keys (regression):
      steps:
      - Run:
          code: |
            doc = s.load('', s.EmptyDict() | s.MapPattern(
              s.Str(),
              s.EmptyDict() | s.Map({
                s.Optional('b'): s.Seq(s.Int()),
              })
            ))
            doc['a'] = {}
            doc['a']['b'] = ['9']
            assert doc.data == {'a': {'b': [9]}}, doc.data
            assert doc.as_yaml() == 'a:\n  b:\n  - 9\n', doc.as_yaml()
            # Second assignment doesn't occur...
            doc['a']['b'] = ['9', '10']
            assert doc.data == {'a': {'b': [9, 10]}}, doc.data
            assert doc.as_yaml() == 'a:\n  b:\n  - 9\n  - 10\n', doc.as_yaml()
            # If and only if another node is overwritten.  This was a bug due
            # to mismatched _ruamelparsed objects.
            doc['b'] = {'b': ['11']}
            assert doc['a']['b'].data == [9, 10], doc.data
            assert doc['b']['b'].data == [11], doc.data
            assert doc.as_yaml() == 'a:\n  b:\n  - 9\n  - 10\nb:\n  b:\n  - 11\n', doc.as_yaml()

    For empty sequence, must instead assign whole sequence as key:
      steps:
      - Run: |-
          doc = s.load('a:', s.Map({'a': s.EmptyList() | s.Seq(s.Int())}))
          doc['a'] = [1, 2, 3]
          assert doc['a'].data == [1, 2, 3], repr(doc.data)


    Can assign from string:
      steps:
      - Run: |-
          doc = s.load('a: 9', s.Map({
            'a': s.Str(),
            s.Optional('b'): s.Int(),
          }))
          doc['b'] = '9'
          assert doc['b'] == 9


    Try update applies several valid changes as one modification:
      steps:
      - Run: |-
          doc = s.load('# comment\nthreshold: 5\nname: old\n', s.Map({
            'threshold': s.Int(),
            s.Optional('name'): s.Str(),
          }))
          doc.try_update({'threshold': '42', 'name': 'new'})
          assert doc['threshold'].data == 42
          assert type(doc['threshold'].data) is int
          assert doc['threshold'].text == '42'
          assert doc['name'].data == 'new'
          assert '# comment' in doc.as_yaml()

    Try update with invalid change leaves document untouched and editable:
      steps:
      - Run: |-
          doc = s.load('# comment\nthreshold: 5\n', s.Map({
            'threshold': s.Int(),
            s.Optional('name'): s.Str(),
          }))
          before = doc.as_yaml()
          try:
            doc.try_update({'threshold': 'not-an-int', 'name': 'x'})
            raise AssertionError('should have raised')
          except s.exceptions.YAMLSerializationError:
            pass
          assert doc.as_yaml() == before
          assert doc['threshold'].data == 5
          assert doc['threshold'].text == '5'
          assert 'name' not in doc.data
          doc['threshold'] = 7
          assert doc['threshold'].data == 7

    Try update is atomic when one of several changes is invalid:
      steps:
      - Run: |-
          doc = s.load('threshold: 5\nname: old\n', s.Map({
            'threshold': s.Int(),
            'name': s.Str(),
          }))
          try:
            doc.try_update({'threshold': '99', 'name': 5})
            raise AssertionError('should have raised')
          except s.exceptions.YAMLSerializationError:
            pass
          assert doc.data == {'threshold': 5, 'name': 'old'}, doc.data
          assert doc.as_yaml() == 'threshold: 5\nname: old\n', doc.as_yaml()
